"""Read/write the experimental voice-movement settings for the panel.

Lets you turn voice-driven locomotion on/off and tune the (deliberately small) speed
and per-command duration. OFF by default — when on, "move forward/back/left/right",
"turn left/right" and "stop" drive the G1 a short, bounded distance. The robot must be
standing in Main/Regular mode. Written to ``.env``, applied on restart.
"""
from __future__ import annotations

from . import env_file, paths

_TRUE = ("1", "true", "yes", "on")


def _get_bool(key: str, default: bool) -> bool:
    raw = env_file.get(paths.ENV_FILE, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE


def _get_float(key: str, default: float) -> float:
    try:
        return float(env_file.get(paths.ENV_FILE, key))
    except (TypeError, ValueError):
        return default


def get_config() -> dict:
    return {
        "enabled": _get_bool("MOVEMENT_COMMANDS_ENABLED", False),
        "speed": _get_float("MOVE_SPEED", 0.2),
        "yaw": _get_float("MOVE_YAW", 0.4),
        "duration_s": _get_float("MOVE_DURATION_S", 1.5),
    }


# Safe bounds — imported from the runtime's single source of truth (app/movement.py)
# so the panel limits can't drift from the runtime clamp. "speed" maps to BOTH forward
# (vx) and strafe (vy), so cap it at the smaller of the two (MAX_VY) to avoid silently
# exceeding the strafe clamp.
try:
    from app.movement import MAX_VX, MAX_VY, MAX_VYAW, MAX_DURATION_S
    SPEED_MAX, YAW_MAX, DUR_MAX = min(MAX_VX, MAX_VY), MAX_VYAW, MAX_DURATION_S
except Exception:  # app package not importable from the panel host — safe fallback
    SPEED_MAX, YAW_MAX, DUR_MAX = 0.3, 0.8, 3.0


def _clamped(updates: dict, key: str, val, lo: float, hi: float) -> None:
    try:
        f = float(val)
        if lo <= f <= hi:
            updates[key] = str(f)
    except (TypeError, ValueError):
        pass


def set_config(enabled=None, speed=None, yaw=None, duration_s=None) -> bool:
    """Write any provided settings to .env. Returns True iff something changed."""
    updates: dict[str, str] = {}
    if enabled is not None:
        updates["MOVEMENT_COMMANDS_ENABLED"] = "true" if enabled else "false"
    if speed is not None:
        _clamped(updates, "MOVE_SPEED", speed, 0.05, SPEED_MAX)
    if yaw is not None:
        _clamped(updates, "MOVE_YAW", yaw, 0.1, YAW_MAX)
    if duration_s is not None:
        _clamped(updates, "MOVE_DURATION_S", duration_s, 0.3, DUR_MAX)
    if updates:
        env_file.update(paths.ENV_FILE, updates)
    return bool(updates)
