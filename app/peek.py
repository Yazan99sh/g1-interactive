"""Detect a 'peek' request — the visitor asks the robot to LOOK at / SHOW them something.

When matched (and the camera + PEEK are enabled), the pipeline announces "let me take a
look", grabs one head-camera frame, and describes what it sees out loud. The reply lives
only in the conversation context (no photos are saved).

Pure (no robot/DDS imports) so it's unit-testable. Conservative matching: it fires on a
genuine look/show request, not on every mention of "see" — e.g. "I see what you mean" or
"look it up online" (a web search) must NOT trigger it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# A peek request can be a short sentence ("can you look at the table and tell me what's
# on it"), but not a paragraph — cap to avoid firing inside long unrelated speech.
MAX_PEEK_TOKENS = 20

# ---- English: an explicit look/show request -------------------------------------
# "look up" is excluded (that's a web search). "I see / I see what you mean" excluded
# (needs "can/do you see", "what do you see").
_EN = [
    re.compile(r"\b(take|have)\s+a\s+look\b", re.I),
    re.compile(r"\blook\s+(at|around|over\s+there|here|there|to\s+your)\b", re.I),
    re.compile(r"\b(can|could|will|would)\s+you\s+(please\s+)?see\b", re.I),
    re.compile(r"\bwhat\s+(do|can)\s+you\s+see\b", re.I),
    re.compile(r"\bdo\s+you\s+see\b", re.I),
    re.compile(r"\bshow\s+me\s+(what|the|that|your|around)\b", re.I),
    re.compile(r"\bwhat'?s\s+(in\s+front\s+of\s+you|over\s+there|around\s+you|on\s+the)\b", re.I),
    re.compile(r"\b(use\s+your\s+)?(camera|eyes)\b", re.I),
]

# ---- Arabic: an explicit look/see request ---------------------------------------
# Imperative look verbs (شوف/شف/بص/انظر/اطلع/تطلع/تفرج) or "what do you see".
_AR = [
    re.compile(r"\b(شوف|شف|بص|انظر|أنظر|اطلع|تطلع|تفرج|تفرّج)\b"),
    re.compile(r"(ماذا|ايش|إيش|شو|وش|وشو)\s*(ترى|تشوف|تشاهد|شايف)"),
    re.compile(r"هل\s*(ترى|تشوف|تشاهد)"),
    re.compile(r"(استخدم|شغّل|شغل)\s*(الكاميرا|عينيك)"),
]


@dataclass
class PeekRequest:
    query: str  # the visitor's utterance, used as the question for the vision model


def parse_peek_intent(text: str) -> Optional[PeekRequest]:
    """Return a PeekRequest if the utterance asks the robot to look/show, else None."""
    if not text or not text.strip():
        return None
    t = text.strip()
    if len(t.split()) > MAX_PEEK_TOKENS:
        return None
    for rx in _EN:
        if rx.search(t):
            return PeekRequest(query=t)
    for rx in _AR:
        if rx.search(t):
            return PeekRequest(query=t)
    return None
