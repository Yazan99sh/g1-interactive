"""Grab one still frame from the G1 head camera (for the 'peek' feature).

Two ways to get a frame (see CAMERA_SOURCE in config):

* ``G1DdsCamera`` (default) — the head RealSense is owned by the on-robot ``videohub``
  service, which serves frames over DDS. We fetch via the SDK ``VideoClient`` — the same
  channel the arm/speaker already use. This is the reliable path: opening ``/dev/video*``
  directly collides with videohub ("device busy"), and videohub auto-respawns if killed.
* ``G1Camera`` (http) — a tiny helper on the Jetson (``tools/jetson_camera_server.py``)
  serves one JPEG over HTTP at ``CAMERA_SNAPSHOT_URL``. Plain HTTP GET (no SDK), so it
  also works from the dev PC — but only if videohub is stopped (it owns the device).

``NullCamera`` is the no-op used when peeking is disabled — the established real/Null
client idiom (cf. ``robot/locomotion.py``).
"""
from __future__ import annotations

import asyncio
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


class G1DdsCamera:
    """Fetches a JPEG from the robot's head camera over DDS (videohub ``VideoClient``).

    Requires DDS to be initialised first (see ``robot/dds.py``) and a reachable robot.
    ``GetImageSample()`` is a blocking DDS RPC, so it runs in an executor to keep the
    event loop responsive. Never raises — returns None on any failure.
    """

    def __init__(self, timeout_s: float = 3.0) -> None:
        from unitree_sdk2py.go2.video.video_client import VideoClient

        self._client = VideoClient()
        self._client.SetTimeout(timeout_s)
        self._client.Init()
        self.enabled = True
        log.info("G1DdsCamera ready (head camera over DDS / videohub).")

    async def capture(self) -> Optional[bytes]:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._capture_blocking)
        except Exception:
            log_exception(log, "DDS camera capture failed")
            return None

    def _capture_blocking(self) -> Optional[bytes]:
        code, data = self._client.GetImageSample()
        if code != 0 or not data:
            log.warning("DDS camera GetImageSample code=%s (%d bytes)", code, len(data) if data else 0)
            return None
        jpeg = bytes(data)
        if len(jpeg) < 100:  # too small to be a real frame
            log.warning("DDS camera frame empty/too small (%d bytes)", len(jpeg))
            return None
        log.info("DDS camera snapshot OK (%d bytes)", len(jpeg))
        return jpeg

    async def close(self) -> None:
        pass
