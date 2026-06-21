#!/usr/bin/env python3
"""Tiny head-camera snapshot server — RUN THIS ON THE G1 JETSON (PC2, 192.168.123.164).

The G1 has no DDS video service (that was Go2-only); its head camera is an Intel
RealSense on the Jetson over USB. This serves ONE JPEG per request so the voice app on
the other host can "peek": it GETs ``http://<jetson>:8090/snapshot`` and passes the JPEG
to a vision model.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("CAM_PORT", "8090"))
WIDTH = int(os.environ.get("CAM_WIDTH", "640"))
HEIGHT = int(os.environ.get("CAM_HEIGHT", "480"))
QUALITY = int(os.environ.get("CAM_JPEG_QUALITY", "80"))
DEVICE = int(os.environ.get("CAM_DEVICE", "0"))


class Camera:
    """Grabs a single JPEG. Prefers the RealSense; falls back to a V4L2 device."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rs = None
        self._cv = None
        self._backend = self._open()

    def _open(self) -> str:
        try:
            import pyrealsense2 as rs  # type: ignore
            pipeline = rs.pipeline()
            cfg = rs.config()
            cfg.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.bgr8, 30)
            pipeline.start(cfg)
            self._rs = (rs, pipeline)
            print(f"[camera] RealSense color stream {WIDTH}x{HEIGHT} started.")
            return "realsense"
        except Exception as exc:  # noqa: BLE001
            print(f"[camera] RealSense unavailable ({exc}); trying OpenCV V4L2 device {DEVICE}.")
        try:
            import cv2  # type: ignore
            cap = cv2.VideoCapture(DEVICE)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
            if not cap.isOpened():
                raise RuntimeError("VideoCapture did not open")
            self._cv = cap
            print(f"[camera] OpenCV V4L2 device {DEVICE} opened.")
            return "opencv"
        except Exception as exc:  # noqa: BLE001
            print(f"[camera] No camera backend available: {exc}", file=sys.stderr)
            return "none"

    def snapshot(self) -> bytes | None:
        with self._lock:
            try:
                import cv2  # type: ignore
                if self._backend == "realsense":
                    rs, pipeline = self._rs
                    frames = pipeline.wait_for_frames(timeout_ms=2000)
                    color = frames.get_color_frame()
                    if not color:
                        return None
                    import numpy as np  # type: ignore
                    img = np.asanyarray(color.get_data())
                elif self._backend == "opencv":
                    ok, img = self._cv.read()
                    if not ok:
                        return None
                else:
                    return None
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
                return buf.tobytes() if ok else None
            except Exception as exc:  # noqa: BLE001
                print(f"[camera] snapshot failed: {exc}", file=sys.stderr)
                return None


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
    print(f"[camera] serving JPEG snapshots on http://0.0.0.0:{PORT}/snapshot")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[camera] stopping.")


if __name__ == "__main__":
    main()
