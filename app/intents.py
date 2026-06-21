"""Lightweight intent guards that short-circuit the normal answer path (EN + AR).

* ``parse_sleep_intent`` — the visitor tells the robot to go idle / sleep / "that's all",
  so it should drop back to STANDBY and wait for the wake word again.
* ``looks_like_noise`` — the transcription is too short / punctuation-only / a known ASR
  hallucination (ambient noise), so it should be treated as silence, not answered.

Pure and unit-testable; conservative matching with a short token cap so they don't fire
inside ordinary sentences.
"""
from __future__ import annotations

import re

from config import settings

MAX_SLEEP_TOKENS = 7

# ---- "go idle / that's all / goodbye" -------------------------------------------
_EN_SLEEP = re.compile(
    r"\b(go\s+(to\s+)?(idle|standby|sleep)|idle\s+state|go\s+back\s+to\s+(standby|sleep|idle)"
    r"|(that'?s|that\s+is)\s+all|(we'?re|we\s+are)\s+done|good\s?bye|bye\s+bye"
    r"|see\s+you(\s+later)?|talk\s+(to\s+you\s+)?later|(standby|sleep|rest|relax)\s+now"
    r"|\bidle\b)\b", re.I)
# Arabic: imperative sleep/rest verbs (whole-word so clitic-glued forms like بنام don't
# match) + explicit "idle/standby mode" / "goodbye" phrases.
_AR_SLEEP = re.compile(
    r"\b(نام|نم|ارتاح|استرح|خلصنا|انهينا)\b"
    r"|وضع\s*(الخمول|الانتظار|الاستعداد)|روح\s*نام|إلى\s*اللقاء|الى\s*اللقاء|مع\s*السلامة"
    r"|انه[ِي]\s*المحادثة")


def parse_sleep_intent(text: str) -> bool:
    """True if the utterance tells the robot to go idle / sleep / that we're done."""
    if not text or not text.strip():
        return False
    t = text.strip()
    if len(t.split()) > MAX_SLEEP_TOKENS:
        return False  # a long sentence that merely contains "idle"/"done" isn't a command
    return bool(_EN_SLEEP.search(t) or _AR_SLEEP.search(t))


# ---- noise / ASR-hallucination filter -------------------------------------------
_WORD = re.compile(r"\w+", re.UNICODE)


def looks_like_noise(text: str) -> bool:
    """True if the transcription is almost certainly NOT real speech directed at the
    robot — too short, punctuation/symbol-only, or a known Whisper/STT hallucination on
    ambient noise (subtitle artifacts, "you", "thank you", "شكرا"…). Such turns are
    treated as silence so the robot doesn't answer noise and eventually returns to idle."""
    if not text:
        return True
    t = text.strip()
    if not t:
        return True
    words = _WORD.findall(t)
    # Punctuation/symbols only, or below the minimum character length.
    if not words or len(t) < settings.NOISE_MIN_CHARS:
        return True
    # A single very short token ("you", "uh", ".") is usually noise, not an utterance.
    if len(words) == 1 and len(words[0]) <= 2:
        return True
    norm = re.sub(r"[\s\.\!\?\،\؟]+", " ", t.lower()).strip()
    return norm in _NOISE_PHRASES()


def _NOISE_PHRASES() -> set[str]:
    # Common Whisper/gpt-4o-transcribe hallucinations on silence/noise (EN + AR), plus
    # any extra phrases configured via NOISE_BLOCKLIST.
    base = {
        "you", "thank you", "thank you.", "thanks", "thanks for watching",
        "thank you for watching", "please subscribe", "bye", "bye bye", "uh", "um",
        "okay", "ok", ".", "..", "...", "♪", "[music]", "[applause]",
        "شكرا", "شكرا لكم", "شكرا جزيلا", "اشتركوا في القناة",
    }
    return base | {p.strip().lower() for p in settings.NOISE_BLOCKLIST if p.strip()}
