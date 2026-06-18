"""Read/write the speech-latency settings for the panel's Speech tab.

These control how quickly the robot starts talking:

* ``STREAMING_ENABLED`` — stream the LLM reply into speech sentence-by-sentence
  (the robot starts after the first sentence is ready).
* ``TTS_CHUNKING_ENABLED`` / ``TTS_CHUNK_MAX_CHARS`` — split a long reply into small
  pieces sent to ElevenLabs one at a time, so the first audio plays almost instantly.

Values are written to ``.env`` and applied on the next pipeline restart.
"""
from __future__ import annotations

from . import env_file, paths

_TRUE = ("1", "true", "yes", "on")


def _get_bool(key: str, default: bool) -> bool:
    raw = env_file.get(paths.ENV_FILE, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE


def _get_int(key: str, default: int) -> int:
    raw = env_file.get(paths.ENV_FILE, key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_config() -> dict:
    return {
        "streaming": _get_bool("STREAMING_ENABLED", True),
        "chunking": _get_bool("TTS_CHUNKING_ENABLED", True),
        "chunk_max_chars": _get_int("TTS_CHUNK_MAX_CHARS", 180),
    }


# Chunk-size bounds — kept in sync with the Speech tab's number input (index.html).
CHUNK_MIN, CHUNK_MAX = 40, 600


def set_config(streaming=None, chunking=None, chunk_max_chars=None) -> bool:
    """Write any provided settings to .env. Returns True iff something was written."""
    updates: dict[str, str] = {}
    if streaming is not None:
        updates["STREAMING_ENABLED"] = "true" if streaming else "false"
    if chunking is not None:
        updates["TTS_CHUNKING_ENABLED"] = "true" if chunking else "false"
    if chunk_max_chars is not None:
        try:
            n = int(chunk_max_chars)
            if CHUNK_MIN <= n <= CHUNK_MAX:
                updates["TTS_CHUNK_MAX_CHARS"] = str(n)
        except (TypeError, ValueError):
            pass
    if updates:
        env_file.update(paths.ENV_FILE, updates)
    return bool(updates)
