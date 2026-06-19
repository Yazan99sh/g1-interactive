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
    # Which transcription backend to use. "openai" = gpt-4o-transcribe (default).
    # "groq" = Whisper large-v3-turbo on Groq — an OpenAI-compatible endpoint that is
    # much faster (and cheaper) with strong Arabic+English; needs GROQ_API_KEY. Falls
    # back to OpenAI if the selected backend's key is missing. Pick it in the panel.
    STT_BACKEND: str = field(default_factory=lambda: _get("STT_BACKEND", "openai").strip().lower())
    OPENAI_STT_MODEL: str = field(default_factory=lambda: _get("OPENAI_STT_MODEL", "gpt-4o-transcribe"))
    GROQ_API_KEY: str = field(default_factory=lambda: _get("GROQ_API_KEY"))
    GROQ_STT_MODEL: str = field(default_factory=lambda: _get("GROQ_STT_MODEL", "whisper-large-v3-turbo"))

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
    # Chunked speech: split a long reply into small pieces and synth+play them one by
    # one (prefetching the next while the current plays) so the robot starts talking
    # almost immediately instead of waiting for the whole reply to render. The visitor
    # feels no wait. Toggle it (and the piece size) from the control panel's Speech tab.
    TTS_CHUNKING_ENABLED: bool = field(default_factory=lambda: _get_bool("TTS_CHUNKING_ENABLED", True))
    # Target max characters per spoken piece (split on sentence/clause boundaries,
    # never mid-word). Smaller = faster first audio but more requests.
    TTS_CHUNK_MAX_CHARS: int = field(default_factory=lambda: _get_int("TTS_CHUNK_MAX_CHARS", 180))

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
    # Extra wait after a chunk's audio is accounted for, covering DDS transport + the
    # robot's playback-start latency so the next chunked piece doesn't clip this one's
    # tail. If you still hear pieces overlap on the robot, raise this; if there are
    # audible gaps between pieces, lower it. Validate on the real robot.
    ROBOT_SPEAKER_TAIL_DRAIN_MS: int = field(default_factory=lambda: _get_int("ROBOT_SPEAKER_TAIL_DRAIN_MS", 200))
    ARM_GESTURES_ENABLED: bool = field(default_factory=lambda: _get_bool("ARM_GESTURES_ENABLED", True))
    # If true, command the locomotion FSM (LocoClient.Start) at startup so arm
    # actions are accepted. Leave FALSE for safety: put the robot in Main/Regular
    # mode + standing via the R3 remote yourself; gestures are best-effort.
    ARM_ENTER_FSM: bool = field(default_factory=lambda: _get_bool("ARM_ENTER_FSM", False))
    # Do ONE arm move the moment the robot starts talking, then hold it — "one move is
    # enough" — instead of a continuous loop. Set false for no talking gesture at all.
    TALK_GESTURES_ENABLED: bool = field(default_factory=lambda: _get_bool("TALK_GESTURES_ENABLED", True))
    # The single arm move performed when the robot starts talking (first id wins).
    # 23 = right-hand-up (one gentle move). Pick it from the panel's Gestures tab.
    TALK_GESTURE_IDS: list[int] = field(default_factory=lambda: _get_int_list("TALK_GESTURE_IDS", "23"))
    # How long to hold the talking gesture before returning to the neutral pose, even
    # if the robot is still speaking (clamped 1000-3000 ms). A brief hold then back-to-
    # rest looks deliberate; holding a raised hand for a whole long reply looks weird.
    TALK_GESTURE_HOLD_MS: int = field(default_factory=lambda: _get_int("TALK_GESTURE_HOLD_MS", 2000))
    # Arm action played on wake / meet-and-greet ("Aha!"). 25 = wave with the right
    # hand up near the head.
    WAKE_GESTURE_ID: int = field(default_factory=lambda: _get_int("WAKE_GESTURE_ID", 25))
    # Robot onboard mic (experimental, U6-unverified) — raw PCM UDP multicast.
    ROBOT_MIC_GROUP: str = field(default_factory=lambda: _get("ROBOT_MIC_GROUP", "239.168.123.161"))
    ROBOT_MIC_PORT: int = field(default_factory=lambda: _get_int("ROBOT_MIC_PORT", 5555))
    # Local IP of the robot-facing NIC (the host's 192.168.123.x address). Used to
    # bind the multicast join to the right interface on a multi-homed host; blank =
    # let the kernel pick (may join the wrong NIC -> no robot-mic audio).
    ROBOT_MIC_IFACE_IP: str = field(default_factory=lambda: _get("ROBOT_MIC_IFACE_IP", ""))

    # ---- Head LED (state indicator) ----
    # Colour the G1 head LED by pipeline state so you can read the robot's state from
    # across the room. Each is "R,G,B" (0-255). The G1 head glows blue by default, so
    # standby stays blue (the "normal/idle" colour) while each active state gets a
    # clearly different colour. Edit any of these from the panel's Environment tab.
    HEAD_LED_ENABLED: bool = field(default_factory=lambda: _get_bool("HEAD_LED_ENABLED", True))
    LED_STANDBY: list[int] = field(default_factory=lambda: _get_int_list("LED_STANDBY", "0,0,90"))        # calm blue (idle)
    LED_LISTENING: list[int] = field(default_factory=lambda: _get_int_list("LED_LISTENING", "0,220,60"))  # green (your turn)
    LED_THINKING: list[int] = field(default_factory=lambda: _get_int_list("LED_THINKING", "255,120,0"))   # amber (working)
    LED_SPEAKING: list[int] = field(default_factory=lambda: _get_int_list("LED_SPEAKING", "180,0,220"))   # magenta (talking)
    LED_ERROR: list[int] = field(default_factory=lambda: _get_int_list("LED_ERROR", "255,0,0"))           # red (a turn failed)
    # States that "breathe" (smoothly pulse) instead of showing a solid colour — a
    # clear sign the robot is busy. Comma-separated: standby/listening/thinking/speaking.
    # Default = thinking only (no audio plays then, so the extra LED RPCs never contend).
    LED_PULSE_STATES: list[str] = field(
        default_factory=lambda: [s.lower() for s in _get_list("LED_PULSE_STATES", "thinking")]
    )
    LED_PULSE_PERIOD_MS: int = field(default_factory=lambda: _get_int("LED_PULSE_PERIOD_MS", 1600))

    # ---- Dialogflow CX (optional "first answer" before the LLM) ----
    # When enabled, each turn is sent to a Dialogflow CX agent FIRST; a confident
    # intent match is spoken verbatim and the LLM is skipped. Anything CX doesn't match
    # falls through to the LLM. Defaults target the bilingual "Nova-1" agent. Toggle and
    # tune from the panel's Dialogflow tab. detectIntent needs the google-cloud-
    # dialogflow-cx lib + a service-account key; if either is missing it silently
    # disables and the robot just uses the LLM.
    DIALOGFLOW_ENABLED: bool = field(default_factory=lambda: _get_bool("DIALOGFLOW_ENABLED", False))
    DIALOGFLOW_PROJECT: str = field(default_factory=lambda: _get("DIALOGFLOW_PROJECT", "nova-1-474411"))
    DIALOGFLOW_LOCATION: str = field(default_factory=lambda: _get("DIALOGFLOW_LOCATION", "asia-south1"))
    DIALOGFLOW_AGENT_ID: str = field(default_factory=lambda: _get("DIALOGFLOW_AGENT_ID", "acada473-7777-4ae3-a27e-c0218644aaf5"))
    # Path to the Google service-account JSON (runtime needs only the "Dialogflow API
    # Client" role). Blank = use the GOOGLE_APPLICATION_CREDENTIALS env var.
    DIALOGFLOW_KEY_PATH: str = field(default_factory=lambda: _get("DIALOGFLOW_KEY_PATH", ""))
    # Minimum match confidence to accept a CX answer (else fall through to the LLM).
    DIALOGFLOW_CONFIDENCE: float = field(default_factory=lambda: _get_float("DIALOGFLOW_CONFIDENCE", 0.6))

    # ---- Experimental: voice movement commands ----
    # OFF by default. When on, "move forward/back/left/right", "turn left/right" and
    # "stop" drive the G1 a SHORT, time-bounded distance via LocoClient. The robot must
    # be STANDING in Main/Regular mode (R1+X). Conservative speeds — experimental, test
    # carefully and keep clear space around the robot. Toggle from the panel.
    MOVEMENT_COMMANDS_ENABLED: bool = field(default_factory=lambda: _get_bool("MOVEMENT_COMMANDS_ENABLED", False))
    MOVE_SPEED: float = field(default_factory=lambda: _get_float("MOVE_SPEED", 0.2))            # m/s
    MOVE_YAW: float = field(default_factory=lambda: _get_float("MOVE_YAW", 0.4))                # rad/s
    MOVE_DURATION_S: float = field(default_factory=lambda: _get_float("MOVE_DURATION_S", 1.5))  # seconds per command

    # ---- Web search (Brave) ----
    # When on (and BRAVE_SEARCH_API_KEY is set), the robot searches the web for explicit
    # "search / look it up" requests and for questions needing current info (news,
    # weather, prices, recent events). It SAYS it's searching first, in the conversation
    # language. Off automatically if the key is missing.
    WEB_SEARCH_ENABLED: bool = field(default_factory=lambda: _get_bool("WEB_SEARCH_ENABLED", True))
    WEB_SEARCH_COUNT: int = field(default_factory=lambda: _get_int("WEB_SEARCH_COUNT", 5))
    WEB_SEARCH_ANNOUNCE_EN: str = field(default_factory=lambda: _get("WEB_SEARCH_ANNOUNCE_EN", "Sure, let me look that up online."))
    WEB_SEARCH_ANNOUNCE_AR: str = field(default_factory=lambda: _get("WEB_SEARCH_ANNOUNCE_AR", "حسنًا، لحظة، خليني أبحث في الإنترنت."))

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
            "GROQ_API_KEY": (mask(self.GROQ_API_KEY) if self.STT_BACKEND == "groq" else "—"),
            "BRAVE_SEARCH_API_KEY": (mask(self.BRAVE_SEARCH_API_KEY) if self.WEB_SEARCH_ENABLED else "—"),
            "WEB_SEARCH_ENABLED": self.WEB_SEARCH_ENABLED,
            "LLM_BACKEND": self.LLM_BACKEND,
            "STREAMING_ENABLED": self.STREAMING_ENABLED,
            "OPENROUTER_MODEL": self.OPENROUTER_MODEL,
            "STT_BACKEND": self.STT_BACKEND,
            "OPENAI_STT_MODEL": self.OPENAI_STT_MODEL,
            "GROQ_STT_MODEL": self.GROQ_STT_MODEL if self.STT_BACKEND == "groq" else "—",
            "ELEVENLABS_MODEL": self.ELEVENLABS_MODEL,
            "TTS_OUTPUT_FORMAT": self.TTS_OUTPUT_FORMAT,
            "TTS_CHUNKING_ENABLED": self.TTS_CHUNKING_ENABLED,
            "DIALOGFLOW_ENABLED": self.DIALOGFLOW_ENABLED,
            "DIALOGFLOW_AGENT": f"{self.DIALOGFLOW_PROJECT}/{self.DIALOGFLOW_LOCATION}/{self.DIALOGFLOW_AGENT_ID}" if self.DIALOGFLOW_ENABLED else "off",
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
