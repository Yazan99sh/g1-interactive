#!/usr/bin/env python3
"""Tiny head-camera snapshot server — RUN THIS ON THE G1 JETSON (PC2, 192.168.123.164).

The G1 has no DDS video service (that was Go2-only); its head camera is an Intel
RealSense on the Jetson over USB. This serves ONE JPEG per request so the voice app on
the other host can "peek": it GETs ``http://<jetson>:8090/snapshot`` and passes the JPEG
to a vision model.

A dedicated capture thread owns the camera (start + continuous grab + cache the latest
JPEG); the HTTP handlers only read the cached frame. This is deliberate: pyrealsense2
raises "wait_for_frames cannot be called before start()" when start() and
wait_for_frames() run on different threads, and ThreadingHTTPServer handles each request
on a new thread — so every camera call must live on one thread.

Deploy on the Jetson::

    ssh unitree@192.168.123.164        # password: 123
    pip install pyrealsense2 opencv-python      # (opencv only needed for the V4L2 fallback)
    python3 jetson_camera_server.py             # serves on 0.0.0.0:8090

Then set on the voice-app host:  CAMERA_ENABLED=true,
CAMERA_SNAPSHOT_URL=http://192.168.123.164:8090/snapshot  (Vision tab / .env).

Verify the camera first:  rs-enumerate-devices   (or  lsusb | grep -i intel).
If your batch wired the RealSense to PC1 (192.168.123.161) instead, run this there.

Endpoints:  GET /snapshot -> image/jpeg     GET /healthz -> "ok"
Env knobs:  CAM_PORT (8090), CAM_WIDTH (640), CAM_HEIGHT (480), CAM_JPEG_QUALITY (80),
            CAM_DEVICE (V4L2 index for the OpenCV fallback, default 0).
"""
from __future__ import annotations

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("CAM_PORT", "8090"))
WIDTH = int(os.environ.get("CAM_WIDTH", "640"))
HEIGHT = int(os.environ.get("CAM_HEIGHT", "480"))
QUALITY = int(os.environ.get("CAM_JPEG_QUALITY", "80"))
DEVICE = int(os.environ.get("CAM_DEVICE", "0"))


class Camera:
    """Owns the camera on ONE background thread and caches the latest JPEG frame.

    All RealSense/OpenCV calls happen inside ``_run`` (one thread); ``snapshot()`` only
    reads the cached bytes under a lock, so it is safe to call from any HTTP worker
    thread. This sidesteps pyrealsense2's same-thread requirement for
    start()/wait_for_frames().
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: bytes | None = None
        self.backend = "starting"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()
        # Give the backend a moment to open and produce the first frame.
        for _ in range(50):
            if self._latest is not None or self.backend == "none":
                break
            time.sleep(0.1)

    def _open(self):
        """Return (backend_name, grab). grab() returns a BGR ndarray or None."""
        try:
            import numpy as np  # type: ignore
            import pyrealsense2 as rs  # type: ignore
            pipeline = rs.pipeline()
            cfg = rs.config()
            cfg.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.bgr8, 30)
            pipeline.start(cfg)
            print(f"[camera] RealSense color stream {WIDTH}x{HEIGHT} started.")

            def grab():
                frames = pipeline.wait_for_frames(timeout_ms=2000)
                color = frames.get_color_frame()
                return np.asanyarray(color.get_data()) if color else None

            return "realsense", grab
        except Exception as exc:  # noqa: BLE001
            print(f"[camera] RealSense unavailable ({exc}); trying OpenCV V4L2 device {DEVICE}.")
        try:
            import cv2  # type: ignore
            cap = cv2.VideoCapture(DEVICE)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
            if not cap.isOpened():
                raise RuntimeError("VideoCapture did not open")
            print(f"[camera] OpenCV V4L2 device {DEVICE} opened.")

            def grab():
                ok, img = cap.read()
                return img if ok else None

            return "opencv", grab
        except Exception as exc:  # noqa: BLE001
            print(f"[camera] No camera backend available: {exc}", file=sys.stderr)
        return "none", None

    def _run(self) -> None:
        backend, grab = self._open()
        self.backend = backend
        if grab is None:
            return
        try:
            import cv2  # type: ignore
        except Exception as exc:  # noqa: BLE001
            print(f"[camera] OpenCV (cv2) is required to encode JPEG: {exc}", file=sys.stderr)
            self.backend = "none"
            return
        while not self._stop.is_set():
            try:
                img = grab()
            except Exception as exc:  # noqa: BLE001
                print(f"[camera] grab failed: {exc}", file=sys.stderr)
                time.sleep(0.1)
                continue
            if img is None:
                time.sleep(0.03)
                continue
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
            if ok:
                with self._lock:
                    self._latest = buf.tobytes()
            time.sleep(0.02)

    def snapshot(self) -> bytes | None:
        with self._lock:
            return self._latest


camera = Camera()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # quieter logs
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/healthz"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if not self.path.startswith("/snapshot"):
            self.send_response(404)
            self.end_headers()
            return
        jpeg = camera.snapshot()
        if not jpeg:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"no frame")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.end_headers()
        self.wfile.write(jpeg)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[camera] serving JPEG snapshots on http://0.0.0.0:{PORT}/snapshot "
          f"(backend: {camera.backend})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[camera] stopping.")
        camera._stop.set()


if __name__ == "__main__":
    main()
