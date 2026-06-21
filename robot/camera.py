"""Grab one still frame from the G1 head camera (for the 'peek' feature).

Unlike the Go2, the **G1 has no DDS video service** — its head camera is an Intel
RealSense on the onboard Jetson (PC2, 192.168.123.164). So we do NOT read it over DDS.
A tiny helper runs on the Jetson (``tools/jetson_camera_server.py``) and exposes one
JPEG over HTTP; this client just fetches that URL. ``NullCamera`` is the no-op used when
peeking is disabled or no snapshot URL is set — the established real/Null client idiom
(cf. ``robot/locomotion.py``).

The capture is a plain HTTP GET (no robot SDK, no DDS), so it also works from the dev PC
as long as the Jetson helper is reachable.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.logging_setup import get_logger, log_exception
from config import settings

log = get_logger("robot.camera")


class NullCamera:
    """No-op camera used when peeking is off or no snapshot URL is configured."""

    enabled = False

    async def capture(self) -> Optional[bytes]:
        return None

    async def close(self) -> None:
        pass


class G1Camera:
    """Fetches a JPEG from the on-Jetson snapshot endpoint (CAMERA_SNAPSHOT_URL)."""

    def __init__(self, http: httpx.AsyncClient, snapshot_url: str) -> None:
        self.http = http
        self.url = snapshot_url.strip()
        self.enabled = bool(self.url)
        log.info("G1Camera ready (snapshot URL: %s)", self.url or "—")

    async def capture(self) -> Optional[bytes]:
        """Return JPEG bytes from the head camera, or None on any failure (never raises)."""
        if not self.enabled:
            return None
        try:
            r = await self.http.get(self.url, timeout=settings.CAMERA_TIMEOUT_S)
        except Exception:
            log_exception(log, "Camera snapshot request failed")
            return None
        if r.status_code != 200:
            log.warning("Camera snapshot HTTP %s from %s", r.status_code, self.url)
            return None
        data = r.content
        if not data or len(data) < 100:  # too small to be a real frame
            log.warning("Camera snapshot empty/too small (%d bytes)", len(data) if data else 0)
            return None
        log.info("Camera snapshot OK (%d bytes)", len(data))
        return data

    async def close(self) -> None:
        pass
