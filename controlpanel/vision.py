"""Read/write camera + 'peek' settings and live-test the camera, for the Vision tab.

The G1 head camera is reached over HTTP from a tiny helper on the Jetson (see
``tools/jetson_camera_server.py``) — not DDS. This tab toggles peeking on/off, sets the
snapshot URL + vision model + spoken announcements, and a test button fetches one frame
to confirm the helper is reachable. Values are written to ``.env`` (applied on restart).
"""
from __future__ import annotations

from . import env_file, paths

_TRUE = ("1", "true", "yes", "on")


def _get(key: str, default: str = "") -> str:
    v = env_file.get(paths.ENV_FILE, key)
    return v if v not in (None, "") else default


def _get_bool(key: str, default: bool) -> bool:
    raw = env_file.get(paths.ENV_FILE, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE


def get_config() -> dict:
    return {
        "camera_enabled": _get_bool("CAMERA_ENABLED", False),
        "peek_enabled": _get_bool("PEEK_ENABLED", True),
        "snapshot_url": _get("CAMERA_SNAPSHOT_URL", "http://192.168.123.164:8090/snapshot"),
        "vision_model": _get("VISION_MODEL", ""),
        "announce_en": _get("PEEK_ANNOUNCE_EN", "Sure, let me take a look."),
        "announce_ar": _get("PEEK_ANNOUNCE_AR", "حسنًا، خليني أشوف."),
    }


def set_config(camera_enabled=None, peek_enabled=None, snapshot_url=None,
               vision_model=None, announce_en=None, announce_ar=None) -> bool:
    """Write any provided settings to .env. Returns True iff something changed."""
    updates: dict[str, str] = {}
    if camera_enabled is not None:
        updates["CAMERA_ENABLED"] = "true" if camera_enabled else "false"
    if peek_enabled is not None:
        updates["PEEK_ENABLED"] = "true" if peek_enabled else "false"
    if snapshot_url is not None and str(snapshot_url).strip():
        updates["CAMERA_SNAPSHOT_URL"] = str(snapshot_url).strip()
    if vision_model is not None:
        updates["VISION_MODEL"] = str(vision_model).strip()  # blank allowed (= use LLM model)
    if announce_en is not None and str(announce_en).strip():
        updates["PEEK_ANNOUNCE_EN"] = str(announce_en).strip()
    if announce_ar is not None and str(announce_ar).strip():
        updates["PEEK_ANNOUNCE_AR"] = str(announce_ar).strip()
    if updates:
        env_file.update(paths.ENV_FILE, updates)
    return bool(updates)


def test_capture() -> dict:
    """Fetch one frame from the configured snapshot URL to confirm the camera works.
    Reports the byte size (a quick JPEG sanity check). Never raises."""
    url = _get("CAMERA_SNAPSHOT_URL", "")
    if not url:
        return {"ok": False, "detail": "CAMERA_SNAPSHOT_URL is not set"}
    try:
        import httpx
    except Exception:
        return {"ok": False, "detail": "httpx not installed on the panel host"}
    try:
        r = httpx.get(url, timeout=8.0)
    except Exception as e:
        return {"ok": False, "detail": f"request failed: {str(e)[:200]}"}
    if r.status_code != 200:
        return {"ok": False, "detail": f"camera helper returned HTTP {r.status_code}"}
    data = r.content
    is_jpeg = bool(data) and data[:2] == b"\xff\xd8"
    if not data or len(data) < 100:
        return {"ok": False, "detail": "got an empty/too-small response (no frame)"}
    return {"ok": True, "bytes": len(data), "jpeg": is_jpeg, "url": url}
