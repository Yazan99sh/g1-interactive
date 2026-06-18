"""Friendly arm-gesture catalog + read/write of the talking/wake gesture settings.

Lets the control panel show named-gesture dropdowns instead of making the operator
type raw action ids: one move performed when the robot starts talking, and the wake /
meet-and-greet wave. Writes the choices to TALK_GESTURE_IDS / WAKE_GESTURE_ID in .env
(the running pipeline picks them up on the next restart).
"""
from __future__ import annotations

from . import env_file, paths

# Verified-ish G1 arm presets (id -> friendly name). Operators can still type any
# custom id in the Environment tab; confirm ids per firmware with g1_list_actions.py.
CATALOG = [
    {"id": 25, "name": "Wave (face level)"},
    {"id": 26, "name": "Wave (high)"},
    {"id": 27, "name": "Shake hand"},
    {"id": 23, "name": "Right hand up"},
    {"id": 33, "name": "Right hand on heart"},
    {"id": 17, "name": "Clap"},
    {"id": 15, "name": "Both hands up"},
    {"id": 18, "name": "High five"},
    {"id": 22, "name": "Refuse (wave no)"},
]


def _parse_ids(raw: str | None) -> list[int]:
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out


def get_config() -> dict:
    talk = _parse_ids(env_file.get(paths.ENV_FILE, "TALK_GESTURE_IDS"))
    if not talk:
        talk = [23]  # mirror the app default in config.py when .env doesn't set it
    try:
        wake = int(env_file.get(paths.ENV_FILE, "WAKE_GESTURE_ID") or 25)
    except (ValueError, TypeError):
        wake = 25
    return {"catalog": CATALOG, "talk_ids": talk, "wake_id": wake}


def set_config(talk_ids: list | None, wake_id: int | None) -> None:
    updates: dict[str, str] = {}
    if talk_ids is not None:
        ids = []
        for i in talk_ids:
            try:
                ids.append(str(int(i)))
            except (ValueError, TypeError):
                pass
        updates["TALK_GESTURE_IDS"] = ",".join(ids)
    if wake_id is not None:
        try:
            updates["WAKE_GESTURE_ID"] = str(int(wake_id))
        except (ValueError, TypeError):
            pass
    if updates:
        env_file.update(paths.ENV_FILE, updates)
