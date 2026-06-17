"""One conversation turn: Transcribe → Think (KB + LLM) → Respond (TTS + gesture).

This is the Python port of ``super-star``'s VoicePipeline. The controller handles
*when* to listen (standby/wake/idle); this class handles *what to do* with a
captured utterance, and how the robot speaks and gestures the answer.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from ai.knowledge_base import KnowledgeBase
from ai.llm import LLMEngine
from ai.stt import OpenAITranscriber
from ai.tts import ElevenLabsTTS
from app.conversation import ConversationManager
from app.logging_setup import get_logger, log_exception
from app.metrics import record_event
from app.state import Emotion, Language, Reply, Transcription, parse_emotion
from audio.sink import AudioSink
from config import settings
from robot.interfaces import ArmController

log = get_logger("app.pipeline")

_FALLBACK = {
    Language.ENGLISH: "[EMOTION:thoughtful] Sorry, I didn't catch that — could you say it again?",
    Language.ARABIC: "[EMOTION:thoughtful] عفواً، ما فهمت عليك، ممكن تعيد؟",
}


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
    ) -> None:
        self.transcriber = transcriber
        self.llm = llm
        self.tts = tts
        self.kb = kb
        self.conversation = conversation
        self.arm = arm
        self.sink = sink

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

        try:
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

    # ---- think ------------------------------------------------------------
    async def think(self, transcription: Transcription) -> Reply:
        if transcription.language:
            self.conversation.set_language(transcription.language)
        lang = self.conversation.language
        self.conversation.add_user(transcription.text)

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
        pcm, sr = await self.tts.synthesize(reply.text, reply.language)
        if not pcm:
            log.warning("TTS produced no audio; gesturing only.")
        # The arms gesture in a LOOP for the whole duration of the speech (not one
        # quick wave then frozen). ``stop`` is set the moment playback ends; the
        # talk loop is best-effort and must never cancel the core speech.
        stop = asyncio.Event()
        talk_task = asyncio.create_task(self.arm.talk(reply.emotion, stop))
        try:
            await self._play(pcm, sr)
        finally:
            stop.set()
            try:
                await talk_task
            except Exception:
                log.warning("talk loop error (non-fatal)", exc_info=True)
            await self.arm.relax()

    async def _play(self, pcm: bytes, sample_rate: int) -> None:
        if pcm:
            await self.sink.play(pcm, sample_rate)

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
