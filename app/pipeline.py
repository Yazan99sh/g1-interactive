"""One conversation turn: Transcribe → Think (KB + LLM) → Respond (TTS + gesture).

This is the Python port of ``super-star``'s VoicePipeline. The controller handles
*when* to listen (standby/wake/idle); this class handles *what to do* with a
captured utterance, and how the robot speaks and gestures the answer.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from ai.dialogflow import DialogflowClient
from ai.knowledge_base import KnowledgeBase
from ai.llm import LLMEngine
from ai.search import WebSearchClient, search_query, to_context
from ai.stt import OpenAITranscriber
from ai.text_chunk import split_for_tts
from ai.tts import ElevenLabsTTS
from app.conversation import ConversationManager
from app.logging_setup import get_logger, log_exception
from app.metrics import record_event
from app.movement import MovementCommand, parse_movement
from app.state import Emotion, Language, Reply, Transcription, parse_emotion, _EMOTION_TAG
from audio.sink import AudioSink
from config import settings
from robot.interfaces import ArmController
from robot.led import LedIndicator
from robot.locomotion import NullLocomotion

log = get_logger("app.pipeline")

_FALLBACK = {
    Language.ENGLISH: "[EMOTION:thoughtful] Sorry, I didn't catch that — could you say it again?",
    Language.ARABIC: "[EMOTION:thoughtful] عفواً، ما فهمت عليك، ممكن تعيد؟",
}

# A "sentence" = run of text up to and including a terminator (. ! ? … ؟) or newline.
# Used to chunk the streamed reply so we can start speaking after the first sentence.
_SENT_RE = re.compile(r"[^.!?…؟\n]*[.!?…؟\n]+")


def _strip_tag_keep_spacing(text: str) -> str:
    """Remove the [EMOTION:x] tag(s) but keep the spacing that follows, so a streamed
    delta isn't glued onto the previous word (parse_emotion() .strip()s). Uses the same
    tolerant matcher as parse_emotion so odd formats never reach the speaker."""
    return _EMOTION_TAG.sub("", text).lstrip()


def _extract_sentences(buf: str) -> tuple[list[str], str]:
    """Split ``buf`` into (complete_sentences, trailing_remainder)."""
    sentences: list[str] = []
    last = 0
    for m in _SENT_RE.finditer(buf):
        chunk = buf[last:m.end()].strip()
        if chunk:
            sentences.append(chunk)
        last = m.end()
    return sentences, buf[last:]


@dataclass
class TurnOutcome:
    had_speech: bool
    user_text: str = ""
    reply_text: str = ""
    error: Optional[str] = None


class ConversationPipeline:
    def __init__(
        self,
        transcriber: OpenAITranscriber,
        llm: LLMEngine,
        tts: ElevenLabsTTS,
        kb: KnowledgeBase,
        conversation: ConversationManager,
        arm: ArmController,
        sink: AudioSink,
        led: Optional[LedIndicator] = None,
        dialogflow: Optional[DialogflowClient] = None,
        locomotion=None,
        search: Optional[WebSearchClient] = None,
    ) -> None:
        self.transcriber = transcriber
        self.llm = llm
        self.tts = tts
        self.kb = kb
        self.conversation = conversation
        self.arm = arm
        self.sink = sink
        # Shared with the controller; off-robot/dev defaults to a no-op indicator.
        self.led = led or LedIndicator(None)
        # Optional Dialogflow CX "first answer" stage (None = always use KB/LLM).
        self.dialogflow = dialogflow
        # Optional experimental voice-driven locomotion (no-op unless enabled + on robot).
        self.locomotion = locomotion or NullLocomotion()
        # Optional Brave web search (None = never search).
        self.search = search

    # ---- listening result -> spoken reply ---------------------------------
    async def handle_audio(self, pcm: bytes, sample_rate: int) -> TurnOutcome:
        """Full turn for a captured utterance. Empty transcription => no_speech."""
        t0 = time.monotonic()
        # Auto-detect language EVERY turn (no hard hint) so a visitor can switch
        # English<->Arabic mid-conversation; detect_language() then drives the
        # conversation language. (A forced `language=` biases gpt-4o-transcribe so
        # strongly it pins the session to one language — see code review.)
        transcription = await self.transcriber.transcribe(pcm, sample_rate)
        if transcription.error:
            # STT API failure (not silence) — keep the conversation open so a
            # transient outage doesn't push us toward standby.
            log.warning("STT failed — keeping conversation open (not counted as silence).")
            return TurnOutcome(had_speech=True, error="stt-failed")
        if transcription.is_blank:
            log.info("Empty transcription — treating as silence.")
            return TurnOutcome(had_speech=False)

        # Experimental: a movement order ("move forward", "وقف"…) is handled here,
        # before the normal answer path, when enabled and running on the robot.
        if settings.MOVEMENT_COMMANDS_ENABLED and self.locomotion.enabled:
            mv = parse_movement(transcription.text)
            if mv is not None:
                lang = transcription.language or self.conversation.language
                ack = await self._do_movement(mv, lang)
                return TurnOutcome(had_speech=True, user_text=transcription.text, reply_text=ack)

        # Web search: if the visitor asks to search or needs current info, announce it
        # and answer from live web results instead of the model's own knowledge.
        if self.search and self.search.enabled:
            try:
                reply = await self._maybe_web_search(transcription)
            except Exception:
                log_exception(log, "Web search turn failed")
                reply = None
            if reply is not None:
                await self.respond(reply)
                ms_total = (time.monotonic() - t0) * 1000
                log.info("Turn done (web search) in %.0f ms", ms_total)
                record_event(
                    settings.LOG_DIR, user=transcription.text, reply=reply.text,
                    lang=reply.language.value, emotion=(reply.emotion or Emotion.NEUTRAL).value,
                    from_kb=False, ms_total=int(ms_total),
                    stt_audio_s=round(len(pcm) / (sample_rate * 2), 2) if sample_rate else 0.0,
                    user_chars=len(transcription.text), reply_chars=len(reply.text),
                )
                return TurnOutcome(had_speech=True, user_text=transcription.text, reply_text=reply.text)

        try:
            if settings.STREAMING_ENABLED:
                reply = await self.converse_streaming(transcription)
            else:
                reply = await self.think(transcription)
                await self.respond(reply)
        except Exception:
            log_exception(log, "Turn failed during think/respond")
            return TurnOutcome(had_speech=True, user_text=transcription.text, error="turn-failed")

        ms_total = (time.monotonic() - t0) * 1000
        log.info("Turn done in %.0f ms", ms_total)
        record_event(
            settings.LOG_DIR,
            user=transcription.text,
            reply=reply.text,
            lang=reply.language.value,
            emotion=(reply.emotion or Emotion.NEUTRAL).value,
            from_kb=reply.from_knowledge_base,
            ms_total=int(ms_total),
            stt_audio_s=round(len(pcm) / (sample_rate * 2), 2) if sample_rate else 0.0,
            user_chars=len(transcription.text),
            reply_chars=len(reply.text),
        )
        return TurnOutcome(had_speech=True, user_text=transcription.text, reply_text=reply.text)

    # ---- experimental movement -------------------------------------------
    async def _do_movement(self, cmd: MovementCommand, language: Language) -> str:
        """Acknowledge out loud and perform one short, bounded move at the same time."""
        ack = cmd.ack_ar if language is Language.ARABIC else cmd.ack_en
        self.led.set_state("speaking")
        log.info("Movement command: %s (%s)", cmd.kind, language.value)
        try:
            await asyncio.gather(
                self.say(ack, language, emotion=Emotion.PLAYFUL, gesture=False),
                self.locomotion.execute(cmd),
                return_exceptions=True,
            )
        finally:
            await self.locomotion.stop()
        return ack

    # ---- web search (optional, announced) ---------------------------------
    async def _maybe_web_search(self, transcription: Transcription) -> Optional[Reply]:
        """If the utterance asks for a search or needs current info, ANNOUNCE that we're
        searching (in the conversation language), fetch live web results, and answer from
        them. Returns the Reply (caller speaks it), or None to fall through to the
        normal Dialogflow/KB/LLM path."""
        query = search_query(transcription.text)
        if not query:
            return None
        if transcription.language:
            self.conversation.set_language(transcription.language)
        lang = self.conversation.language
        self.conversation.add_user(transcription.text)
        # Say we're searching, in the visitor's language, while the request is in flight.
        announce = (settings.WEB_SEARCH_ANNOUNCE_AR if lang is Language.ARABIC
                    else settings.WEB_SEARCH_ANNOUNCE_EN)
        self.led.set_state("thinking")
        # Fetch while we speak the announcement, so the search overlaps the TTS.
        search_task = asyncio.create_task(self.search.search(query, lang))
        await self.say(announce, lang, emotion=Emotion.THOUGHTFUL, gesture=False)
        try:
            results = await search_task
        except Exception:
            log_exception(log, "Brave search task failed")
            results = []
        if results:
            kb_context = to_context(query, results) + (
                "\n\nUse the WEB SEARCH RESULTS above to answer — they are current. Answer "
                "briefly and naturally for speech, say you found it online, and never read "
                "out URLs.")
        else:
            kb_context = ("(The web search returned no results — tell the visitor briefly "
                          "that you couldn't find anything online about that.)")
        messages = self.conversation.build_messages(transcription.text, kb_context)
        raw = await self.llm.complete(messages)
        if not raw.strip():
            raw = _FALLBACK[lang]
        emotion, clean = parse_emotion(raw)
        self.conversation.add_assistant(clean)
        log.info("Reply [%s/web] (%d results): %s", lang.value, len(results), clean[:80])
        return Reply(text=clean, emotion=emotion or Emotion.NEUTRAL, language=lang,
                     meta={"source": "web", "query": query})

    # ---- dialogflow (optional first answer) -------------------------------
    async def _dialogflow_reply(self, text: str, lang: Language) -> Optional[Reply]:
        """Try Dialogflow CX first. Returns a Reply (and records it in history) on a
        confident match, or None to fall through to the KB / LLM."""
        if not (self.dialogflow and self.dialogflow.enabled):
            return None
        answer = await self.dialogflow.answer(text, lang)
        if not answer:
            return None
        self.conversation.add_assistant(answer)
        log.info("Reply [%s/dialogflow]: %s", lang.value, answer[:80])
        return Reply(text=answer, emotion=Emotion.HAPPY, language=lang,
                     from_knowledge_base=True, meta={"source": "dialogflow"})

    # ---- think ------------------------------------------------------------
    async def think(self, transcription: Transcription) -> Reply:
        if transcription.language:
            self.conversation.set_language(transcription.language)
        lang = self.conversation.language
        self.conversation.add_user(transcription.text)

        # Dialogflow CX first (if enabled) — a confident structured answer skips the LLM.
        df = await self._dialogflow_reply(transcription.text, lang)
        if df is not None:
            return df

        # KB-strict: verbatim FAQ answer, skip the LLM.
        if settings.KB_STRICT:
            match = self.kb.match_faq(transcription.text, lang)
            if match:
                faq_answer, faq_lang = match
                # Honour any [EMOTION:x] tag the author wrote, and speak in the FAQ's
                # own language (its voice), not just the running conversation language.
                emotion, clean = parse_emotion(faq_answer)
                self.conversation.add_assistant(clean)
                return Reply(text=clean, emotion=emotion or Emotion.HAPPY,
                             language=faq_lang or lang, from_knowledge_base=True)

        kb_context = self.kb.context_for(transcription.text)
        messages = self.conversation.build_messages(transcription.text, kb_context)
        raw = await self.llm.complete(messages)
        if not raw.strip():
            raw = _FALLBACK[lang]

        emotion, clean = parse_emotion(raw)
        self.conversation.add_assistant(clean)
        log.info("Reply [%s/%s]: %s", lang.value, (emotion or Emotion.NEUTRAL).value, clean[:80])
        return Reply(text=clean, emotion=emotion or Emotion.NEUTRAL, language=lang)

    # ---- respond (speak + gesture together) -------------------------------
    async def respond(self, reply: Reply) -> None:
        """Speak a fully-known reply with one arm move; chunk long text for low latency."""
        await self._speak(reply.text, reply.language, reply.emotion or Emotion.NEUTRAL)

    async def _speak(self, text: str, language: Language, emotion: Emotion) -> None:
        """Set the speaking LED, do ONE arm move (held for the whole reply), and play
        the speech — chunked into small pieces if it's long so audio starts fast."""
        self.led.set_state("speaking")
        # ``stop`` is set the instant playback ends; the gesture is best-effort and
        # must never cancel the core speech.
        stop = asyncio.Event()
        talk_task = asyncio.create_task(self.arm.talk(emotion, stop))
        try:
            if settings.TTS_CHUNKING_ENABLED and len(text) > settings.TTS_CHUNK_MAX_CHARS:
                await self._play_chunked(text, language)
            else:
                pcm, sr = await self.tts.synthesize(text, language)
                if not pcm:
                    log.warning("TTS produced no audio; gesturing only.")
                await self._play(pcm, sr)
        finally:
            stop.set()
            try:
                await talk_task
            except Exception:
                log.warning("talk gesture error (non-fatal)", exc_info=True)
            await self.arm.relax()

    async def _play_chunked(self, text: str, language: Language) -> None:
        """Synthesize the reply in small pieces and play them back-to-back, prefetching
        the next piece while the current one plays — so first audio starts fast and
        there's no SYNTH wait between pieces (just a small natural beat at the boundary
        from the speaker's tail-drain, which prevents pieces overlapping)."""
        pieces = split_for_tts(text, settings.TTS_CHUNK_MAX_CHARS)
        if not pieces:
            return
        log.info("Chunked TTS: %d piece(s) (≤%d chars each).", len(pieces), settings.TTS_CHUNK_MAX_CHARS)
        next_task = asyncio.create_task(self.tts.synthesize(pieces[0], language))
        try:
            for i in range(len(pieces)):
                try:
                    pcm, sr = await next_task
                except Exception:
                    log_exception(log, "Chunk TTS failed (skipping piece)")
                    pcm, sr = b"", self.tts.sample_rate
                # Kick off the next piece's synth before we start playing this one.
                next_task = (
                    asyncio.create_task(self.tts.synthesize(pieces[i + 1], language))
                    if i + 1 < len(pieces) else None
                )
                if pcm:
                    await self._play(pcm, sr)
        finally:
            if next_task is not None:
                next_task.cancel()
                try:
                    await next_task
                except BaseException:
                    pass

    async def _play(self, pcm: bytes, sample_rate: int) -> None:
        if pcm:
            await self.sink.play(pcm, sample_rate)

    # ---- streaming think+respond (low latency) ----------------------------
    async def converse_streaming(self, transcription: Transcription) -> Reply:
        """Stream the LLM reply into speech sentence-by-sentence.

        The robot starts talking after the *first* sentence is ready instead of
        waiting for the whole reply + whole TTS. KB-strict verbatim answers and any
        empty-stream case fall back to the proven one-shot path.
        """
        if transcription.language:
            self.conversation.set_language(transcription.language)
        lang = self.conversation.language
        self.conversation.add_user(transcription.text)

        # Dialogflow CX first — speak a confident structured answer and skip streaming.
        df = await self._dialogflow_reply(transcription.text, lang)
        if df is not None:
            await self.respond(df)
            return df

        # KB-strict verbatim answer — short, no need to stream.
        if settings.KB_STRICT:
            match = self.kb.match_faq(transcription.text, lang)
            if match:
                faq_answer, faq_lang = match
                emotion, clean = parse_emotion(faq_answer)
                self.conversation.add_assistant(clean)
                reply = Reply(text=clean, emotion=emotion or Emotion.HAPPY,
                              language=faq_lang or lang, from_knowledge_base=True)
                await self.respond(reply)
                return reply

        kb_context = self.kb.context_for(transcription.text)
        messages = self.conversation.build_messages(transcription.text, kb_context)

        spoken, emotion = await self._stream_speak(messages, lang)
        text = " ".join(spoken).strip()

        if not text:
            # Streaming produced nothing (e.g. provider doesn't support SSE) — fall
            # back to a normal completion so the robot still answers.
            log.warning("Streaming yielded no text — falling back to one-shot complete().")
            raw = await self.llm.complete(messages)
            if not raw.strip():
                raw = _FALLBACK[lang]
            emotion, text = parse_emotion(raw)
            emotion = emotion or Emotion.NEUTRAL
            reply = Reply(text=text, emotion=emotion, language=lang)
            await self.respond(reply)
            self.conversation.add_assistant(text)
            return reply

        self.conversation.add_assistant(text)
        log.info("Reply [%s/%s] (streamed): %s", lang.value, emotion.value, text[:80])
        return Reply(text=text, emotion=emotion, language=lang)

    async def _stream_speak(self, messages, lang: Language) -> tuple[list[str], Emotion]:
        """Run the LLM stream (producer) and speak sentences (consumer) concurrently,
        gesturing for the whole duration. Returns (spoken_sentences, emotion)."""
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        state: dict[str, Emotion] = {}

        async def producer() -> None:
            buf, head, resolved = "", "", False
            try:
                async for delta in self.llm.stream(messages):
                    if not delta:
                        continue
                    if not resolved:
                        head += delta
                        em, _ = parse_emotion(head)
                        # Commit the emotion once the leading [EMOTION:x] tag is fully
                        # seen (a ']' appeared) or it's clearly absent (got long).
                        if em is not None or "]" in head or len(head) >= 48:
                            state["emotion"] = em or Emotion.NEUTRAL
                            resolved, buf = True, _strip_tag_keep_spacing(head)
                        else:
                            continue
                    else:
                        buf += delta
                    sentences, buf = _extract_sentences(buf)
                    for s in sentences:
                        await queue.put(s)
            except Exception:
                log_exception(log, "LLM stream producer failed")
            finally:
                if not resolved:  # very short reply with no terminator yet
                    em, _ = parse_emotion(head)
                    state["emotion"] = em or Emotion.NEUTRAL
                    buf = _strip_tag_keep_spacing(head)
                tail = buf.strip()
                if tail:
                    await queue.put(tail)
                await queue.put(None)  # sentinel: stream finished

        async def consumer() -> list[str]:
            stop = asyncio.Event()
            talk_task: Optional[asyncio.Task] = None
            spoken: list[str] = []
            parts: deque[str] = deque()        # pending pieces of the current sentence
            finished = False                   # producer sentinel reached

            async def next_piece() -> Optional[str]:
                """Next speakable piece in order; pulls a sentence (splitting a run-on
                one) when the buffer is empty. None once the stream is exhausted."""
                nonlocal finished
                while not parts:
                    if finished:
                        return None
                    sentence = await queue.get()
                    if sentence is None:
                        finished = True
                        return None
                    spoken.append(sentence)
                    if settings.TTS_CHUNKING_ENABLED and len(sentence) > settings.TTS_CHUNK_MAX_CHARS:
                        parts.extend(split_for_tts(sentence, settings.TTS_CHUNK_MAX_CHARS))
                    else:
                        parts.append(sentence)
                return parts.popleft()

            async def fetch_and_synth():
                piece = await next_piece()
                if piece is None:
                    return None
                return await self.tts.synthesize(piece, lang)

            prefetch: Optional[asyncio.Task] = None
            try:
                # Synth the first piece, then keep synthesising the NEXT piece while the
                # current one plays — first audio starts fast and there's no SYNTH wait
                # between pieces (the prefetch overlaps playback, even on the streaming
                # path; a small tail-drain beat at each boundary prevents overlap).
                result = await fetch_and_synth()
                while result is not None:
                    if talk_task is None:  # first real audio: light up + do one move
                        self.led.set_state("speaking")
                        talk_task = asyncio.create_task(
                            self.arm.talk(state.get("emotion", Emotion.NEUTRAL), stop)
                        )
                    pcm, sr = result
                    prefetch = asyncio.create_task(fetch_and_synth())
                    if pcm:
                        await self._play(pcm, sr)
                    result = await prefetch
                    prefetch = None
            finally:
                if prefetch is not None:
                    prefetch.cancel()
                    try:
                        await prefetch
                    except BaseException:
                        pass
                stop.set()
                if talk_task is not None:
                    try:
                        await talk_task
                    except Exception:
                        log.warning("talk gesture error (non-fatal)", exc_info=True)
                await self.arm.relax()
            return spoken

        prod = asyncio.create_task(producer())
        try:
            spoken = await consumer()
        finally:
            # Always tear the producer (and its open LLM stream) down — whether the
            # consumer finished, raised, or was cancelled. On success prod is already
            # done, so awaiting it just surfaces any producer error.
            if not prod.done():
                prod.cancel()
            try:
                await prod
            except (asyncio.CancelledError, Exception):
                pass
        return spoken, state.get("emotion", Emotion.NEUTRAL)

    # ---- speak an arbitrary line (used for wake-ack "Aha!") ---------------
    async def say(self, text: str, language: Language, emotion: Emotion = Emotion.HAPPY,
                  gesture: bool = True) -> None:
        pcm, sr = await self.tts.synthesize(text, language)
        try:
            tasks = [self._play(pcm, sr)]
            if gesture:
                tasks.append(self.arm.express(emotion))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    log.warning("say branch error: %r", r)
        finally:
            if gesture:
                await self.arm.relax()
