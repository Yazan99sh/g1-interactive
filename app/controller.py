"""Master conversation controller — the top-level state machine.

    STANDBY  ──("Hi Robot")──►  ACK("Aha!" + wave)  ──►  CONVERSE
       ▲                                                     │
       └──────────  (10 consecutive silent turns)  ◄─────────┘

* STANDBY: keep listening cheaply (VAD-gated), transcribe only when someone
  speaks, and wait for a wake phrase. Arms rest.
* ACK: the robot says "Aha!" and waves, then the conversation opens.
* CONVERSE: Listen → Think → Respond, looping. Each turn with no speech bumps a
  counter; ``IDLE_AFTER_SILENT_TURNS`` silent turns drop back to STANDBY until the
  next "Hi Robot".
"""
from __future__ import annotations

import asyncio

from app.conversation import ConversationManager
from app.logging_setup import get_logger, log_exception
from app.pipeline import ConversationPipeline
from app.state import Emotion, Language, PipelineState, detect_language
from audio.mic import MicSource, record_utterance
from audio.vad import UtteranceSegmenter
from audio.wake import WakeWordDetector
from ai.stt import OpenAITranscriber
from config import settings
from robot.interfaces import ArmController
from robot.led import LedIndicator

log = get_logger("app.controller")


class Controller:
    def __init__(
        self,
        mic: MicSource,
        transcriber: OpenAITranscriber,
        pipeline: ConversationPipeline,
        conversation: ConversationManager,
        wake_detector: WakeWordDetector,
        arm: ArmController,
        wake_audio=None,
        led: LedIndicator | None = None,
    ) -> None:
        self.mic = mic
        self.transcriber = transcriber
        self.pipeline = pipeline
        self.conversation = conversation
        self.wake_detector = wake_detector
        self.arm = arm
        # Head-LED state indicator — share the same instance the pipeline uses so
        # standby/listening/thinking (here) and speaking (pipeline) all drive one LED.
        self.led = led or pipeline.led
        # Optional offline audio wake detector (openWakeWord). When set, standby uses
        # it instead of STT — no per-phrase transcription cost.
        self.wake_audio = wake_audio
        self.sample_rate = mic.sample_rate
        self.state = PipelineState.STANDBY
        self._stop = False

    # ---- segmenters -------------------------------------------------------
    def _standby_segmenter(self) -> UtteranceSegmenter:
        # Short window so standby keeps cycling and stays responsive.
        return UtteranceSegmenter(
            self.sample_rate, settings.SILENCE_RMS_THRESHOLD, settings.SILENCE_DURATION_MS,
            settings.MIN_RECORDING_MS, settings.STANDBY_WINDOW_MS,
            no_speech_timeout_ms=settings.STANDBY_WINDOW_MS,
        )

    def _active_segmenter(self) -> UtteranceSegmenter:
        return UtteranceSegmenter(
            self.sample_rate, settings.SILENCE_RMS_THRESHOLD, settings.SILENCE_DURATION_MS,
            settings.MIN_RECORDING_MS, settings.MAX_RECORDING_MS,
            no_speech_timeout_ms=settings.LISTEN_NO_SPEECH_TIMEOUT_MS,
        )

    # ---- lifecycle --------------------------------------------------------
    async def run(self) -> None:
        await self.mic.open()
        await self.led.start()
        log.info("Controller running. Say one of %s to wake the robot.", settings.WAKE_WORDS)
        try:
            while not self._stop:
                await self.standby()
                if self._stop:
                    break
                await self.converse()
        finally:
            await self.led.stop()
            await self.mic.close()
            await self.arm.close()
            try:
                await self.pipeline.locomotion.close()
            except Exception:
                pass
            await self.pipeline.sink.close()

    def stop(self) -> None:
        self._stop = True
        try:
            self.mic.flush()  # wake an in-flight read so the loop re-checks _stop
        except Exception:
            pass
        try:
            self.pipeline.sink.stop()
        except Exception:
            pass

    # ---- STANDBY ----------------------------------------------------------
    async def standby(self) -> None:
        self.state = PipelineState.STANDBY
        await self.arm.relax()
        self.led.set_state("standby")
        if self.wake_audio is not None:
            log.info("STANDBY — listening for wake word (openWakeWord, offline)…")
            await self._standby_openwakeword()
        else:
            log.info("STANDBY — waiting for wake word (STT)…")
            await self._standby_stt()

    async def _standby_stt(self) -> None:
        while not self._stop:
            pcm, had_speech = await record_utterance(self.mic, self._standby_segmenter())
            if not had_speech:
                continue
            transcription = await self.transcriber.transcribe(pcm, self.sample_rate, None)
            if transcription.is_blank:
                continue
            if self.wake_detector.matches(transcription.text):
                lang = transcription.language or detect_language(transcription.text) or Language.ENGLISH
                log.info("WAKE — heard '%s' (%s)", transcription.text, lang.value)
                self._begin_session(lang)
                await self._acknowledge(lang)
                return
            log.debug("Ignored (not a wake word): '%s'", transcription.text)

    def _begin_session(self, lang: Language) -> None:
        """Start a conversation on wake — keep prior history if the last exchange was
        recent (same person continuing), else reset (a new visitor)."""
        timeout = settings.SESSION_MEMORY_TIMEOUT_S
        if timeout <= 0 or not self.conversation.history or self.conversation.seconds_idle() > timeout:
            self.conversation.reset()
            log.info("New conversation (fresh memory).")
        else:
            log.info("Continuing conversation — memory kept (idle %.0fs ≤ %ds).",
                     self.conversation.seconds_idle(), timeout)
        self.conversation.set_language(lang)
        # Fresh Dialogflow CX session per visitor so turns share context but a new
        # person starts clean.
        if getattr(self.pipeline, "dialogflow", None):
            self.pipeline.dialogflow.new_session()

    async def _standby_openwakeword(self) -> None:
        # Feed raw mic frames to the local model — no STT in standby. The model is
        # phrase-only, so language auto-detects on the first real turn (default EN).
        self.wake_audio.reset()
        while not self._stop:
            chunk = await self.mic.read_chunk()
            if not chunk:
                continue
            if self.wake_audio.feed(chunk):
                lang = Language.ENGLISH
                log.info("WAKE (openWakeWord)")
                self._begin_session(lang)
                await self._acknowledge(lang)
                return

    async def _acknowledge(self, language: Language) -> None:
        self.led.set_state("speaking")
        ack = settings.WAKE_ACK_TEXT_AR if language is Language.ARABIC else settings.WAKE_ACK_TEXT_EN
        try:
            # Wave hello and say "Aha!" at the same time (one failing branch must
            # not cancel the other).
            await asyncio.gather(
                self.arm.greet(),
                self.pipeline.say(ack, language, emotion=Emotion.EXCITED, gesture=False),
                return_exceptions=True,
            )
            await self.arm.relax()
        except Exception:
            log_exception(log, "Wake acknowledgement failed")

    # ---- CONVERSE ---------------------------------------------------------
    async def converse(self) -> None:
        silent = 0
        limit = settings.IDLE_AFTER_SILENT_TURNS
        log.info("CONVERSE — listening (idle after %d silent turns).", limit)
        while not self._stop and silent < limit:
            self.state = PipelineState.LISTENING
            self.led.set_state("listening")
            pcm, had_speech = await record_utterance(self.mic, self._active_segmenter())
            if not had_speech:
                silent += 1
                log.info("Silent turn %d/%d", silent, limit)
                continue

            self.state = PipelineState.THINKING
            self.led.set_state("thinking")
            outcome = await self.pipeline.handle_audio(pcm, self.sample_rate)
            if outcome.error:
                # Blink red briefly so a failure is visible, then carry on listening.
                await self.led.flash("error", 600)
            if not outcome.had_speech:
                silent += 1
                log.info("Silent turn %d/%d (blank transcription)", silent, limit)
                continue
            silent = 0  # real exchange — reset the idle counter

        log.info("No speech for %d turns — returning to STANDBY.", limit)
        self.state = PipelineState.STANDBY
