"""Read/write the teleop-mode master switch for the panel.

Teleop mode is a single override: when ON, the voice app releases every resource that
would fight a VR/arm teleoperator (xr_teleoperate) — arm gestures (and the locomotion
FSM they'd enter), voice-driven movement, and the head camera (peek). Voice (mic +
speaker) and the head LED keep working, so the robot can still talk while teleoperated.

It does NOT change the operator's individual switches; their .env values are preserved
and resume the moment teleop mode is turned back off. Written to ``.env`` (TELEOP_MODE),
applied on the pipeline's next restart.
"""
from __future__ import annotations

from . import env_file, paths

_TRUE = ("1", "true", "yes", "on")


def _get_bool(key: str, default: bool) -> bool:
    raw = env_file.get(paths.ENV_FILE, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE


# The individual switches teleop mode overrides (forces off) while it is on. Surfaced to
# the operator so it's clear exactly what is released; their .env values stay untouched.
SUPPRESSES = ["ARM_GESTURES_ENABLED", "MOVEMENT_COMMANDS_ENABLED", "CAMERA_ENABLED"]


def get_config() -> dict:
    return {
        "enabled": _get_bool("TELEOP_MODE", False),
        "suppresses": SUPPRESSES,
        # The operator's own settings — what resumes when teleop mode is turned off.
        "arm_gestures": _get_bool("ARM_GESTURES_ENABLED", True),
        "movement": _get_bool("MOVEMENT_COMMANDS_ENABLED", False),
        "camera": _get_bool("CAMERA_ENABLED", False),
    }


def set_config(enabled=None) -> bool:
    """Write the teleop-mode switch to .env. Returns True iff it changed."""
    if enabled is None:
        return False
    env_file.update(paths.ENV_FILE, {"TELEOP_MODE": "true" if enabled else "false"})
    return True
