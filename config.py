"""Central configuration for the G1 interactive voice pipeline.

Every tunable lives here so nothing is scattered across modules. Secrets and
deployment-specific values are read from the ``.env`` file (see ``.env.example``).

Import style:  ``from config import settings``  then ``settings.OPENAI_API_KEY``.
A module-level ``settings`` singleton is created at import time.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a hard dep, but be friendly
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    val = os.getenv(name)
    return val if val not in (None, "") else default


def _get_bool(name: str, default: bool = False) -> bool:
    return _get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _get_list(name: str, default: str) -> list[str]:
    return [s.strip() for s in _get(name, default).split(",") if s.strip()]


def _get_int_list(name: str, default: str = "") -> list[int]:
    out: list[int] = []
    for s in _get(name, default).split(","):
        s = s.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError:
            pass
    return out


@dataclass
class Settings:
    """Resolved configuration, populated from the environment."""

    # ---- API keys ----
    OPENAI_API_KEY: str = field(default_factory=lambda: _get("OPENAI_API_KEY"))
    OPENROUTER_API_KEY: str = field(default_factory=lambda: _get("OPENROUTER_API_KEY"))
    GEMINI_API_KEY: str = field(default_factory=lambda: _get("GEMINI_API_KEY"))
    ELEVENLABS_API_KEY: str = field(default_factory=lambda: _get("ELEVENLABS_API_KEY"))
    BRAVE_SEARCH_API_KEY: str = field(default_factory=lambda: _get("BRAVE_SEARCH_API_KEY"))

    # ---- LLM ----
    LLM_BACKEND: str = field(default_factory=lambda: _get("LLM_BACKEND", "openrouter"))
    # Stream the reply sentence-by-sentence into TTS so the robot starts talking as
    # soon as the first sentence is ready (much lower perceived latency). Falls back
    # to the one-shot path automatically if streaming yields nothing.
    STREAMING_ENABLED: bool = field(default_factory=lambda: _get_bool("STREAMING_ENABLED", True))
    OPENROUTER_MODEL: str = field(default_factory=lambda: _get("OPENROUTER_MODEL", "openai/gpt-4o-mini"))
    OPENAI_LLM_MODEL: str = field(default_factory=lambda: _get("OPENAI_LLM_MODEL", "gpt-4o-mini"))
    LLM_TEMPERATURE: float = field(default_factory=lambda: _get_float("LLM_TEMPERATURE", 0.6))
    # 0 = NO hard cap (recommended): the max_tokens param is omitted so the model is
    # never cut off mid-sentence. Control reply length with instructions in
    # prompts/persona.md instead. Set >0 only if you need a strict ceiling.
    LLM_MAX_TOKENS: int = field(default_factory=lambda: _get_int("LLM_MAX_TOKENS", 0))

    # ---- STT ----
    OPENAI_STT_MODEL: str = field(default_factory=lambda: _get("OPENAI_STT_MODEL", "gpt-4o-transcribe"))

    # ---- TTS ----
    ELEVENLABS_MODEL: str = field(default_factory=lambda: _get("ELEVENLABS_MODEL", "eleven_flash_v2_5"))
    ELEVENLABS_VOICE_ID: str = field(default_factory=lambda: _get("ELEVENLABS_VOICE_ID", "DANw8bnAVbjDEHwZIoYa"))
    ELEVENLABS_ARABIC_VOICE_ID: str = field(
        default_factory=lambda: _get("ELEVENLABS_ARABIC_VOICE_ID", _get("ELEVENLABS_VOICE_ID", "DANw8bnAVbjDEHwZIoYa"))
    )
    TTS_OUTPUT_FORMAT: str = field(default_factory=lambda: _get("TTS_OUTPUT_FORMAT", "pcm_16000"))
    TTS_STABILITY: float = field(default_factory=lambda: _get_float("TTS_STABILITY", 0.5))
    TTS_SIMILARITY: float = field(default_factory=lambda: _get_float("TTS_SIMILARITY", 0.75))
    TTS_SPEED: float = field(default_factory=lambda: _get_float("TTS_SPEED", 1.0))

    # ---- Audio / VAD ----
    SAMPLE_RATE: int = field(default_factory=lambda: _get_int("SAMPLE_RATE", 16000))
    SILENCE_RMS_THRESHOLD: float = field(default_factory=lambda: _get_float("SILENCE_RMS_THRESHOLD", 500.0))
    SILENCE_DURATION_MS: int = field(default_factory=lambda: _get_int("SILENCE_DURATION_MS", 900))
    MIN_RECORDING_MS: int = field(default_factory=lambda: _get_int("MIN_RECORDING_MS", 800))
    MAX_RECORDING_MS: int = field(default_factory=lambda: _get_int("MAX_RECORDING_MS", 15000))
    # End an active-listening turn early if no speech is heard (counts as a silent
    # turn toward IDLE_AFTER_SILENT_TURNS). Standby cycles on its own window.
    LISTEN_NO_SPEECH_TIMEOUT_MS: int = field(default_factory=lambda: _get_int("LISTEN_NO_SPEECH_TIMEOUT_MS", 8000))
    STANDBY_WINDOW_MS: int = field(default_factory=lambda: _get_int("STANDBY_WINDOW_MS", 10000))
    MIC_SOURCE: str = field(default_factory=lambda: _get("MIC_SOURCE", "host"))
    MIC_DEVICE: str = field(default_factory=lambda: _get("MIC_DEVICE", ""))
    AUDIO_SINK: str = field(default_factory=lambda: _get("AUDIO_SINK", "robot"))

    # ---- Wake word / standby ----
    WAKE_WORDS: list[str] = field(
        default_factory=lambda: [w.lower() for w in _get_list(
            "WAKE_WORDS", "hi robot,hey robot,hello robot,هاي روبوت,مرحبا روبوت")]
    )
    WAKE_ACK_TEXT_EN: str = field(default_factory=lambda: _get("WAKE_ACK_TEXT_EN", "Aha!"))
    WAKE_ACK_TEXT_AR: str = field(default_factory=lambda: _get("WAKE_ACK_TEXT_AR", "أها!"))
    IDLE_AFTER_SILENT_TURNS: int = field(default_factory=lambda: _get_int("IDLE_AFTER_SILENT_TURNS", 10))
    # Session memory: on a new wake, KEEP the prior conversation if the last exchange
    # was within this many seconds (same person continuing) — only start fresh after a
    # longer gap (assume a new visitor). 0 = always start fresh on every wake.
    SESSION_MEMORY_TIMEOUT_S: int = field(default_factory=lambda: _get_int("SESSION_MEMORY_TIMEOUT_S", 180))
    # Wake engine: "stt" (default) transcribes standby utterances and matches WAKE_WORDS
    # (bilingual EN+AR, costs a small STT call per spoken phrase). "openwakeword" runs a
    # local model — zero STT cost in standby, faster trigger, but phrase-only/English
    # (needs the openwakeword package + a model; see OWW_MODEL). Falls back to "stt" if
    # the package/model can't load.
    WAKE_ENGINE: str = field(default_factory=lambda: _get("WAKE_ENGINE", "stt").strip().lower())
    # openWakeWord model: a built-in name (e.g. "hey_jarvis", "alexa") or a path to a
    # custom .onnx/.tflite trained for "Hi Robot". Built-ins won't match "Hi Robot" —
    # train one (see CONTROL_PANEL.md / openWakeWord docs) and point OWW_MODEL at it.
    OWW_MODEL: str = field(default_factory=lambda: _get("OWW_MODEL", "hey_jarvis"))
    OWW_THRESHOLD: float = field(default_factory=lambda: _get_float("OWW_THRESHOLD", 0.5))
    OWW_INFERENCE_FRAMEWORK: str = field(default_factory=lambda: _get("OWW_INFERENCE_FRAMEWORK", "onnx"))

    # ---- Robot / DDS ----
    ROBOT_ENABLED: bool = field(default_factory=lambda: _get_bool("ROBOT_ENABLED", True))
    DDS_DOMAIN: int = field(default_factory=lambda: _get_int("DDS_DOMAIN", 0))
    DDS_INTERFACE: str = field(default_factory=lambda: _get("DDS_INTERFACE", "ens37"))
    ROBOT_SPEAKER_VOLUME: int = field(default_factory=lambda: _get_int("ROBOT_SPEAKER_VOLUME", 80))
    ARM_GESTURES_ENABLED: bool = field(default_factory=lambda: _get_bool("ARM_GESTURES_ENABLED", True))
    # If true, command the locomotion FSM (LocoClient.Start) at startup so arm
    # actions are accepted. Leave FALSE for safety: put the robot in Main/Regular
    # mode + standing via the R3 remote yourself; gestures are best-effort.
    ARM_ENTER_FSM: bool = field(default_factory=lambda: _get_bool("ARM_ENTER_FSM", False))
    # Keep the arms gesturing for the WHOLE time the robot is talking (looped),
    # instead of one quick wave then freezing. Always-on movement (any emotion).
    TALK_GESTURES_ENABLED: bool = field(default_factory=lambda: _get_bool("TALK_GESTURES_ENABLED", True))
    # Pause between looped talk gestures (ms). The loop bails out of this pause the
    # instant speech ends, so it never delays the relax.
    TALK_GESTURE_GAP_MS: int = field(default_factory=lambda: _get_int("TALK_GESTURE_GAP_MS", 250))
    # Safety cap: most gestures to play in a single reply (0 = unlimited). Stops a
    # runaway loop if a reply is extremely long.
    TALK_GESTURE_MAX_PER_REPLY: int = field(default_factory=lambda: _get_int("TALK_GESTURE_MAX_PER_REPLY", 40))
    # Arm-action ids cycled while talking. Default = 25 face-wave + 23 right-hand-up
    # (gentle, conversational). Set in .env to change; clear to "" only in code to fall
    # back to the per-emotion palette.
    TALK_GESTURE_IDS: list[int] = field(default_factory=lambda: _get_int_list("TALK_GESTURE_IDS", "25,23"))
    # Robot onboard mic (experimental, U6-unverified) — raw PCM UDP multicast.
    ROBOT_MIC_GROUP: str = field(default_factory=lambda: _get("ROBOT_MIC_GROUP", "239.168.123.161"))
    ROBOT_MIC_PORT: int = field(default_factory=lambda: _get_int("ROBOT_MIC_PORT", 5555))
    # Local IP of the robot-facing NIC (the host's 192.168.123.x address). Used to
    # bind the multicast join to the right interface on a multi-homed host; blank =
    # let the kernel pick (may join the wrong NIC -> no robot-mic audio).
    ROBOT_MIC_IFACE_IP: str = field(default_factory=lambda: _get("ROBOT_MIC_IFACE_IP", ""))

    # ---- Knowledge base ----
    KB_STRICT: bool = field(default_factory=lambda: _get_bool("KB_STRICT", False))

    # ---- Logging ----
    LOG_LEVEL: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))

    # ---- Derived paths ----
    BASE_DIR: Path = BASE_DIR
    LOG_DIR: Path = BASE_DIR / "logs"
    KNOWLEDGE_DIR: Path = BASE_DIR / "knowledge"
    PROMPTS_DIR: Path = BASE_DIR / "prompts"

    def redacted(self) -> dict:
        """A dict of settings safe to log (keys masked)."""
        def mask(v: str) -> str:
            return f"{v[:6]}…{v[-4:]}" if len(v) > 12 else ("set" if v else "MISSING")

        return {
            "OPENAI_API_KEY": mask(self.OPENAI_API_KEY),
            "OPENROUTER_API_KEY": mask(self.OPENROUTER_API_KEY),
            "ELEVENLABS_API_KEY": mask(self.ELEVENLABS_API_KEY),
            "LLM_BACKEND": self.LLM_BACKEND,
            "STREAMING_ENABLED": self.STREAMING_ENABLED,
            "OPENROUTER_MODEL": self.OPENROUTER_MODEL,
            "OPENAI_STT_MODEL": self.OPENAI_STT_MODEL,
            "ELEVENLABS_MODEL": self.ELEVENLABS_MODEL,
            "TTS_OUTPUT_FORMAT": self.TTS_OUTPUT_FORMAT,
            "SAMPLE_RATE": self.SAMPLE_RATE,
            "MIC_SOURCE": self.MIC_SOURCE,
            "AUDIO_SINK": self.AUDIO_SINK,
            "ROBOT_ENABLED": self.ROBOT_ENABLED,
            "DDS_INTERFACE": self.DDS_INTERFACE,
            "ARM_GESTURES_ENABLED": self.ARM_GESTURES_ENABLED,
            "TALK_GESTURES_ENABLED": self.TALK_GESTURES_ENABLED,
            "WAKE_WORDS": self.WAKE_WORDS,
            "IDLE_AFTER_SILENT_TURNS": self.IDLE_AFTER_SILENT_TURNS,
        }


settings = Settings()
