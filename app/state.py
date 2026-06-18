"""Shared domain model: pipeline states, language, emotion, and small value types.

Mirrors the proven ``super-star`` design (PipelineState / Emotion / [EMOTION:x]
tags) but in plain Python so it can drive the G1 over DDS.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Optional


class PipelineState(str, enum.Enum):
    """High-level state of the conversation loop."""

    STANDBY = "standby"        # waiting for the wake word ("Hi Robot")
    LISTENING = "listening"    # capturing the user's speech
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"      # knowledge base + LLM
    RESPONDING = "responding"  # ElevenLabs speech + arm gestures
    ERROR = "error"


class Language(str, enum.Enum):
    ENGLISH = "en"
    ARABIC = "ar"

    @property
    def display_name(self) -> str:
        return "Arabic" if self is Language.ARABIC else "English"


class Emotion(str, enum.Enum):
    """Emotional colouring of a reply — drives arm gesture + LED choice."""

    NEUTRAL = "neutral"
    HAPPY = "happy"
    EXCITED = "excited"
    CURIOUS = "curious"
    THOUGHTFUL = "thoughtful"
    SURPRISED = "surprised"
    PLAYFUL = "playful"
    SAD = "sad"
    ANGRY = "angry"
    SLEEPY = "sleepy"

    @classmethod
    def from_str(cls, value: str) -> Optional["Emotion"]:
        try:
            return cls(value.strip().lower())
        except ValueError:
            return None


# Tolerant tag matcher: catches [EMOTION:happy], [EMOTION: happy], [emotion=happy],
# [ EMOTION : very happy ], [EMOTION] — anything an LLM might emit — so the bracket is
# ALWAYS removed before TTS (otherwise the robot tries to *speak* the brackets).
_EMOTION_TAG = re.compile(r"\[\s*emotion\s*[:=]?\s*([a-z_]+)?[^\]]*\]", re.IGNORECASE)
_ARABIC_RANGE = re.compile(r"[؀-ۿݐ-ݿ]")


def parse_emotion(text: str) -> tuple[Optional[Emotion], str]:
    """Extract an ``[EMOTION:happy]``-style tag; return (emotion, clean_text).

    Strips EVERY tag occurrence (lenient to spacing/format) so none reach the speaker.
    """
    match = _EMOTION_TAG.search(text)
    if not match:
        return None, text.strip()
    word = match.group(1)
    emotion = Emotion.from_str(word) if word else None
    clean = _EMOTION_TAG.sub("", text).strip()
    return emotion, clean


def detect_language(text: str) -> Optional[Language]:
    """Infer language from script. Arabic if >30% of letters are Arabic, else English.

    Returns None when there are no letters (caller keeps prior language).
    Matches the ``super-star`` OpenAiSttProvider heuristic.
    """
    if not text:
        return None
    arabic = len(_ARABIC_RANGE.findall(text))
    letters = sum(1 for ch in text if ch.isalpha())
    if letters == 0:
        return None
    return Language.ARABIC if (arabic / letters) > 0.3 else Language.ENGLISH


@dataclass
class Transcription:
    text: str
    language: Optional[Language] = None
    # True when STT actually FAILED (network/HTTP/parse error) — distinct from a
    # genuinely silent/empty result, so the controller doesn't count an API outage
    # as a "silent turn" toward the idle-to-standby counter.
    error: bool = False

    @property
    def is_blank(self) -> bool:
        return not self.text or not self.text.strip()


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class Reply:
    """The finished 'thinking' result, ready to speak + gesture."""

    text: str
    emotion: Optional[Emotion] = None
    language: Language = Language.ENGLISH
    from_knowledge_base: bool = False
    meta: dict = field(default_factory=dict)
