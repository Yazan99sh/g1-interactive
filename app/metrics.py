"""Per-turn event log for the control panel (transcript + rough cost view).

Appends one JSON object per line to ``logs/events.jsonl``. This is intentionally
*append-only* and crash-proof: a failure to write an event must never disturb the
conversation, so every call is wrapped and only logged at DEBUG.

Event schema (``type == "turn"``):
    ts          float   unix seconds
    type        str     "turn"
    user        str     what the visitor said (transcript)
    reply       str     what the robot said back (tag stripped)
    lang        str     "en" | "ar"
    emotion     str     emotion that drove the gesture
    from_kb     bool    answered verbatim from the knowledge base (no LLM)
    ms_total    int     wall-clock for the whole turn
    stt_audio_s float   seconds of audio sent to STT (drives STT cost)
    user_chars  int     characters transcribed
    reply_chars int     characters spoken (drives TTS cost)

The panel computes a rough cost from a configurable price table; we deliberately
keep raw counts here (not dollars) so pricing can change without touching the app.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger

log = get_logger("app.metrics")

EVENTS_FILE = "events.jsonl"


def record_event(log_dir: Path, **fields: Any) -> None:
    """Append one event. Never raises (best-effort, DEBUG-logged on failure)."""
    try:
        fields.setdefault("ts", time.time())
        fields.setdefault("type", "turn")
        path = Path(log_dir) / EVENTS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("Could not write event (non-fatal).", exc_info=True)
