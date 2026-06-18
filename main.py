"""G1 Interactive Voice Pipeline — entry point.

Wires the dependency graph from ``config`` and runs the conversation controller.

Robot pieces (speaker / arms / mic over DDS) are optional and degrade gracefully:
if the Unitree SDK or robot isn't reachable, the app falls back to host speakers /
no-op arms so the whole cloud pipeline can be developed and demoed without the G1.

Run:   python main.py
Stop:  Ctrl+C
"""
from __future__ import annotations

import asyncio
import signal

import httpx

from ai.knowledge_base import KnowledgeBase
from ai.llm import LLMEngine
from ai.stt import OpenAITranscriber
from ai.tts import ElevenLabsTTS
from app.controller import Controller
from app.conversation import ConversationManager
from app.logging_setup import get_logger, log_exception, setup_logging
from app.pipeline import ConversationPipeline
from audio.mic import HostMic, MicSource
from audio.sink import AudioSink, HostSpeaker
from audio.wake import WakeWordDetector
from config import settings
from robot.interfaces import ArmController, NullArmController
from robot.led import LedIndicator

log = get_logger("main")


def _init_dds_if_needed() -> None:
    need_dds = settings.ROBOT_ENABLED and (
        settings.AUDIO_SINK == "robot"
        or settings.MIC_SOURCE == "robot"
        or settings.ARM_GESTURES_ENABLED
    )
    if not need_dds:
        log.info("DDS not needed (robot components disabled).")
        return
    try:
        from robot.dds import init_dds
        init_dds(settings.DDS_INTERFACE, settings.DDS_DOMAIN)
        log.info("DDS initialised on interface '%s' (domain %d).",
                 settings.DDS_INTERFACE, settings.DDS_DOMAIN)
    except Exception:
        log_exception(log, "DDS init failed — robot components will fall back to host/no-op")


def build_arm() -> ArmController:
    if settings.ROBOT_ENABLED and settings.ARM_GESTURES_ENABLED:
        try:
            from robot.arm_gestures import G1ArmGestures
            return G1ArmGestures()
        except Exception:
            log_exception(log, "G1 arm controller unavailable — using NullArmController")
    return NullArmController()


def build_sink() -> AudioSink:
    if settings.ROBOT_ENABLED and settings.AUDIO_SINK == "robot":
        try:
            from robot.speaker import G1Speaker
            return G1Speaker()
        except Exception:
            log_exception(log, "G1 speaker unavailable — using HostSpeaker")
    return HostSpeaker()


def build_mic() -> MicSource:
    if settings.ROBOT_ENABLED and settings.MIC_SOURCE == "robot":
        try:
            from robot.mic_robot import G1Mic
            return G1Mic(settings.SAMPLE_RATE)
        except Exception:
            log_exception(log, "G1 mic unavailable — using HostMic")
    return HostMic(settings.SAMPLE_RATE, settings.MIC_DEVICE)


def build_wake_audio():
    """Offline wake detector (openWakeWord) if WAKE_ENGINE=openwakeword, else None
    (the controller then uses the STT wake matcher). Falls back to None on failure."""
    if settings.WAKE_ENGINE == "openwakeword":
        try:
            from audio.wake_audio import OpenWakeWordDetector
            return OpenWakeWordDetector()
        except Exception:
            log_exception(log, "openWakeWord unavailable — falling back to STT wake engine")
    return None


async def amain() -> None:
    setup_logging(settings.LOG_DIR, settings.LOG_LEVEL)
    log.info("=== G1 Interactive Voice Pipeline starting ===")
    log.info("Config: %s", settings.redacted())

    # DDS must be initialised before any robot client is constructed.
    _init_dds_if_needed()

    http = httpx.AsyncClient()
    controller: Controller | None = None
    try:
        transcriber = OpenAITranscriber(http)
        tts = ElevenLabsTTS(http)
        llm = LLMEngine(http)
        kb = KnowledgeBase(settings.KNOWLEDGE_DIR)
        conversation = ConversationManager()

        loop = asyncio.get_running_loop()
        # Robot client Init() are blocking DDS RPCs (~10s) — construct them off the
        # event-loop thread so a slow/unreachable robot can't stall startup.
        arm = await loop.run_in_executor(None, build_arm)
        sink = await loop.run_in_executor(None, build_sink)
        # openWakeWord model load can take a couple seconds — keep it off the loop.
        wake_audio = await loop.run_in_executor(None, build_wake_audio)
        mic = build_mic()  # constructor does no I/O (socket join is in open())
        log.info("Audio sink=%s, mic=%s, arm=%s, wake=%s",
                 type(sink).__name__, type(mic).__name__, type(arm).__name__,
                 "openWakeWord" if wake_audio else "stt")

        led = LedIndicator(sink)  # head-LED state indicator (shared by both)
        pipeline = ConversationPipeline(transcriber, llm, tts, kb, conversation, arm, sink, led=led)
        controller = Controller(
            mic=mic,
            transcriber=transcriber,
            pipeline=pipeline,
            conversation=conversation,
            wake_detector=WakeWordDetector(settings.WAKE_WORDS),
            arm=arm,
            wake_audio=wake_audio,
            led=led,
        )

        run_task = asyncio.create_task(controller.run())

        def _shutdown() -> None:
            controller.stop()      # cooperative flag + wake mic + stop playback
            run_task.cancel()      # cancel in-flight awaits (e.g. a 60s LLM call)

        for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, _shutdown)
            except (NotImplementedError, RuntimeError):
                pass  # Windows ProactorEventLoop — rely on KeyboardInterrupt instead

        try:
            await run_task
        finally:
            # Ensure clean teardown on any exit (incl. KeyboardInterrupt cancelling
            # the await): cancel the controller so its finally closes mic/arm/sink.
            if not run_task.done():
                run_task.cancel()
                try:
                    await run_task
                except BaseException:
                    pass
    except asyncio.CancelledError:
        pass
    except Exception:
        log_exception(log, "Fatal error in main loop")
    finally:
        await http.aclose()
        log.info("=== G1 Interactive Voice Pipeline stopped ===")


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        get_logger("main").info("Interrupted — shutting down.")


if __name__ == "__main__":
    main()
