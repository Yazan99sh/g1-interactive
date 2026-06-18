"""Split a long reply into small speakable pieces for chunked TTS.

The goal is *time to first audio*: instead of sending one big string to ElevenLabs
and waiting for the whole thing to render, we cut the reply into short pieces on
natural boundaries and synth/play them one at a time (see ``pipeline._play_chunked``).
The robot starts talking after the first short piece, so the visitor feels no wait.

Boundaries, in priority order: sentence terminators (``. ! ? … ؟`` + newline),
then clause punctuation (``, ، ; :``), then word spaces, and only as a last resort
a hard cut — so a piece is never split in the middle of a word.

Works for both English and Arabic (Arabic comma ``،`` and question mark ``؟`` are
included). Pure string logic, no I/O — easy to unit-test.
"""
from __future__ import annotations

import re

# A sentence = text up to and including a terminator (Latin + Arabic) or newline.
_SENTENCE_RE = re.compile(r"[^.!?…؟\n]*[.!?…؟\n]+|[^.!?…؟\n]+$")
# Clause break points used to subdivide a single over-long sentence.
_CLAUSE_RE = re.compile(r"[^,،؛;:]*[,،؛;:]+|[^,،؛;:]+$")


def _split_units(text: str, pattern: re.Pattern) -> list[str]:
    return [m.strip() for m in pattern.findall(text) if m.strip()]


def _hard_wrap(unit: str, max_chars: int) -> list[str]:
    """Last-resort split of a too-long unit on word spaces (then a hard cut if a
    single 'word' itself exceeds max_chars)."""
    words = unit.split()
    if not words:
        return []
    pieces: list[str] = []
    cur = ""
    for w in words:
        if len(w) > max_chars:  # a single mega-token — hard-cut it
            if cur:
                pieces.append(cur)
                cur = ""
            for i in range(0, len(w), max_chars):
                pieces.append(w[i:i + max_chars])
            continue
        candidate = f"{cur} {w}".strip()
        if len(candidate) > max_chars and cur:
            pieces.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        pieces.append(cur)
    return pieces


def split_for_tts(text: str, max_chars: int = 180) -> list[str]:
    """Return ``text`` as a list of pieces, each ideally ≤ ``max_chars`` characters,
    cut on the most natural boundary available. Empty input → ``[]``; short input →
    a single piece."""
    text = (text or "").strip()
    if not text:
        return []
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    # Break too-long sentences down further on clause punctuation / spaces first.
    units: list[str] = []
    for sentence in _split_units(text, _SENTENCE_RE):
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue
        for clause in _split_units(sentence, _CLAUSE_RE):
            if len(clause) <= max_chars:
                units.append(clause)
            else:
                units.extend(_hard_wrap(clause, max_chars))

    # Greedily re-pack adjacent small units up to max_chars so we don't over-fragment
    # (e.g. many tiny sentences become a few well-sized pieces).
    pieces: list[str] = []
    cur = ""
    for unit in units:
        candidate = f"{cur} {unit}".strip()
        if cur and len(candidate) > max_chars:
            pieces.append(cur)
            cur = unit
        else:
            cur = candidate
    if cur:
        pieces.append(cur)
    return pieces
