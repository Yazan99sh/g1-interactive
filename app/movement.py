"""Parse a short, bounded movement command from a user utterance (EN + AR).

EXPERIMENTAL and OFF by default (see MOVEMENT_COMMANDS_ENABLED). When enabled, the
pipeline runs this BEFORE the normal think/answer path: if the utterance is a clear,
terse movement order ("move forward", "تقدم للأمام", "turn left", "وقف"…) the robot
does a small, time-bounded move and acknowledges, instead of treating it as a question.

This module is pure (no robot/DDS imports) so it's unit-testable. It is deliberately
CONSERVATIVE — a missed command is harmless (the robot just answers normally), but a
false trigger physically moves the robot, so we only fire on terse imperatives:

* a hard **length cap** (real commands are short, not sentences);
* **English** requires a motion verb + direction, word-boundaried, with idiom guards
  ("go ahead", "move forward with the plan", "go forward to the exit" do NOT fire);
* **Arabic** requires a real motion/turn **verb** (whole-word, so clitic-glued nouns
  like ‏باليمين/‏اليمين/‏التقدم/‏وقفة don't match) before any direction is read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from config import settings

# Commands are terse imperatives; anything longer is almost certainly a sentence, not
# an order. A missed long-form command is safe; a false move is not.
MAX_CMD_TOKENS = 6
MAX_STOP_TOKENS = 4

# Hard safety bounds for any drive — the SINGLE source of truth, imported by both the
# runtime clamp (robot/locomotion.py) and the panel's input limits (controlpanel/
# movement.py) so they can't drift. Velocities are conservative for an indoor greeter.
MAX_VX = 0.4          # m/s forward/back
MAX_VY = 0.3          # m/s strafe
MAX_VYAW = 0.8        # rad/s turn
MAX_DURATION_S = 3.0  # seconds per command


@dataclass
class MovementCommand:
    kind: str           # forward | backward | left | right | turn_left | turn_right | stop
    vx: float           # m/s   (+forward)
    vy: float           # m/s   (+left, strafe)
    vyaw: float         # rad/s (+left turn)
    duration_s: float
    ack_en: str
    ack_ar: str


# ---- English: motion verb + direction, word-boundaried -----------------------
# 'ahead' is intentionally NOT a forward direction (kills the filler "go ahead").
# Forward/backward have a trailing-object guard so idioms ("move forward with the
# plan", "go forward to the exit", "back up the files") don't fire.
_EN = [
    ("turn_left",  re.compile(r"\b(turn|rotate|spin|pivot)\s+(to\s+the\s+)?left\b", re.I)),
    ("turn_right", re.compile(r"\b(turn|rotate|spin|pivot)\s+(to\s+the\s+)?right\b", re.I)),
    ("forward",    re.compile(r"\b(go|move|walk|step|head)\s+(forward|straight|forwards)\b"
                              r"(?!\s+(with|in|into|to|toward|towards|on|about))", re.I)),
    ("backward",   re.compile(r"\b((go|move|walk|step)\s+(back|backward|backwards)|back\s+up)\b"
                              r"(?!\s+(with|in|to|the|my|your|a|an|all|everything|data|file|files|plan))", re.I)),
    ("left",       re.compile(r"\b(go|move|walk|step|slide|strafe)\s+(to\s+the\s+)?left\b", re.I)),
    ("right",      re.compile(r"\b(go|move|walk|step|slide|strafe)\s+(to\s+the\s+)?right\b", re.I)),
]
# Stop is matched only on very short utterances (a stop order is never a long sentence).
_EN_STOP = re.compile(r"\b(stop(\s+moving)?|halt|freeze|stand\s+still|hold\s+still|don'?t\s+move)\b", re.I)

# ---- Arabic: a real motion/turn VERB is required before any direction is read ----
# Whole-word (\b) verbs so clitic-glued NOUNS never trigger:
#   باليمين / اليمين (right), التقدم (progress), وقفة / توقفت (a pause / it stopped) → no match.
# Once a verb is confirmed, the direction may be matched loosely (clitics like لل/ال),
# because ordinary non-command sentences lack the verb.
_AR_STOP = re.compile(r"\b(قف|وقف|توقف|أوقف|اوقف)\b")
_AR_MOVE_VERB = re.compile(r"\b(امشي|امش|تحرك|تحرّك|روح|اتجه|سر|تعال|تقدم|أتقدم|اتقدم|ارجع|إرجع|تراجع|تأخر)\b")
_AR_TURN_VERB = re.compile(r"\b(لف|لفّ|استدر|استدير|دور|درّ)\b")
_AR_FWD = ("امام", "أمام", "قدام", "قدّام")
_AR_BWD = ("خلف", "ورا", "وراء")
_AR_LEFT = ("يسار", "شمال")
_AR_RIGHT = ("يمين",)
_AR_FWD_VERB = {"تقدم", "أتقدم", "اتقدم", "امشي", "امش", "تعال"}
_AR_BWD_VERB = {"ارجع", "إرجع", "تراجع", "تأخر"}


def _acks() -> dict:
    return {
        "forward":    ("Moving forward.", "أتقدّم للأمام."),
        "backward":   ("Moving back.", "أرجع للخلف."),
        "left":       ("Stepping to the left.", "أتحرّك لليسار."),
        "right":      ("Stepping to the right.", "أتحرّك لليمين."),
        "turn_left":  ("Turning left.", "ألتفت لليسار."),
        "turn_right": ("Turning right.", "ألتفت لليمين."),
        "stop":       ("Stopping.", "أتوقّف."),
    }


def _make(kind: str) -> MovementCommand:
    s = settings.MOVE_SPEED
    y = settings.MOVE_YAW
    d = settings.MOVE_DURATION_S
    vel = {
        "forward":    (s, 0.0, 0.0),
        "backward":   (-s, 0.0, 0.0),
        "left":       (0.0, s, 0.0),
        "right":      (0.0, -s, 0.0),
        "turn_left":  (0.0, 0.0, y),
        "turn_right": (0.0, 0.0, -y),
        "stop":       (0.0, 0.0, 0.0),
    }[kind]
    en, ar = _acks()[kind]
    # 'stop' is instantaneous (no timed drive).
    return MovementCommand(kind, vel[0], vel[1], vel[2], 0.0 if kind == "stop" else d, en, ar)


def _has(text: str, needles) -> bool:
    return any(n in text for n in needles)


def _parse_ar(t: str) -> Optional[str]:
    if _AR_STOP.search(t):
        return "stop"
    move = _AR_MOVE_VERB.search(t)
    turn = _AR_TURN_VERB.search(t)
    if turn:
        if _has(t, _AR_RIGHT):
            return "turn_right"
        if _has(t, _AR_LEFT):
            return "turn_left"
    if move:
        if _has(t, _AR_FWD):
            return "forward"
        if _has(t, _AR_BWD):
            return "backward"
        if _has(t, _AR_RIGHT):
            return "right"
        if _has(t, _AR_LEFT):
            return "left"
        verb = move.group(1)
        if verb in _AR_FWD_VERB:
            return "forward"
        if verb in _AR_BWD_VERB:
            return "backward"
    return None


def parse_movement(text: str) -> Optional[MovementCommand]:
    """Return a MovementCommand if the utterance is a clear movement order, else None."""
    if not text or not text.strip():
        return None
    t = text.strip()
    n_tokens = len(t.split())
    if n_tokens > MAX_CMD_TOKENS:
        return None  # too long to be a terse command

    # English (boundary-aware verb + direction; stop only on a short utterance).
    if _EN_STOP.search(t) and n_tokens <= MAX_STOP_TOKENS:
        return _make("stop")
    for kind, rx in _EN:
        if rx.search(t):
            return _make(kind)

    # Arabic (verb required before a direction is read).
    kind = _parse_ar(t)
    if kind is not None:
        return _make(kind)
    return None
