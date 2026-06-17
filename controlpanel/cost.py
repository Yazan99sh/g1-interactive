"""Rough API-cost estimation from ``logs/events.jsonl``.

These are ESTIMATES for an at-a-glance idea of spend — token counts are approximated
from character counts (~4 chars/token) since the app logs characters, not tokens.
Prices are USD and editable from the panel (persisted to state/prices.json).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths

DEFAULT_PRICES: dict[str, float] = {
    "stt_per_min": 0.006,        # OpenAI gpt-4o-transcribe
    "llm_in_per_1k_tok": 0.00015,  # gpt-4o-mini input
    "llm_out_per_1k_tok": 0.0006,  # gpt-4o-mini output
    "tts_per_1k_chars": 0.05,    # ElevenLabs flash (plan-dependent)
}
_PRICES_FILE = paths.STATE_DIR / "prices.json"


def load_prices() -> dict[str, float]:
    prices = dict(DEFAULT_PRICES)
    try:
        if _PRICES_FILE.exists():
            prices.update(json.loads(_PRICES_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return prices


def save_prices(prices: dict[str, float]) -> dict[str, float]:
    merged = dict(DEFAULT_PRICES)
    for key, value in (prices or {}).items():
        if key in DEFAULT_PRICES:
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                pass
    paths.ensure_state_dir()
    _PRICES_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def compute(prices: dict[str, float] | None = None) -> dict[str, Any]:
    prices = prices or load_prices()
    stt_s = tts_chars = llm_in_tok = llm_out_tok = 0.0
    turns = 0
    path = paths.EVENTS_FILE
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") != "turn":
                continue
            turns += 1
            stt_s += float(ev.get("stt_audio_s", 0) or 0)
            tts_chars += float(ev.get("reply_chars", 0) or 0)
            llm_in_tok += float(ev.get("user_chars", 0) or 0) / 4.0
            llm_out_tok += float(ev.get("reply_chars", 0) or 0) / 4.0

    stt_usd = stt_s / 60.0 * prices["stt_per_min"]
    llm_usd = (llm_in_tok / 1000.0 * prices["llm_in_per_1k_tok"]
               + llm_out_tok / 1000.0 * prices["llm_out_per_1k_tok"])
    tts_usd = tts_chars / 1000.0 * prices["tts_per_1k_chars"]
    return {
        "prices": prices,
        "turns": turns,
        "totals": {
            "stt_usd": round(stt_usd, 4),
            "llm_usd": round(llm_usd, 4),
            "tts_usd": round(tts_usd, 4),
            "total_usd": round(stt_usd + llm_usd + tts_usd, 4),
        },
        "note": "estimates",
    }
