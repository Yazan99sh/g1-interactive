#!/usr/bin/env python3
"""Probe the G1 head camera over DDS via videohub — RUN THIS ON THE HOST.

On the G1, ``videohub`` (e.g. videohub_pc4) on the Jetson owns the RealSense and serves
frames over DDS. Opening ``/dev/video*`` directly collides with it ("Device or resource
busy"), and the service auto-respawns if killed. So the right move is to fetch a frame
via the SDK ``VideoClient`` (the same API the Go2 head camera used) instead of the raw
device. If this prints a JPEG, ``peek`` can use DDS and we can drop the Jetson helper
(``tools/jetson_camera_server.py``) entirely.

    python tools/dds_video_test.py [dds_interface]     # default: wlp2s0

Tip: stop the voice pipeline first so only one process touches DDS during the probe.
"""
from __future__ import annotations

import sys

iface = sys.argv[1] if len(sys.argv) > 1 else "wlp2s0"
print(f"[dds-video] ChannelFactoryInitialize(0, {iface!r})")

from unitree_sdk2py.core.channel import ChannelFactoryInitialize  # noqa: E402

ChannelFactoryInitialize(0, iface)

try:
    from unitree_sdk2py.go2.video.video_client import VideoClient  # noqa: E402
except Exception as exc:  # noqa: BLE001
    print(f"[dds-video] Could not import VideoClient: {exc}")
    print("[dds-video] Check the installed unitree_sdk2py layout (it may expose video elsewhere).")
    sys.exit(1)

client = VideoClient()
client.SetTimeout(3.0)
client.Init()

code, data = client.GetImageSample()
n = len(data) if data else 0
print(f"[dds-video] GetImageSample -> code={code}, bytes={n}")

if code == 0 and n > 100:
    out = "/tmp/dds_peek.jpg"
    with open(out, "wb") as f:
        f.write(bytes(data))
    print(f"[dds-video] OK — wrote {out}. Open it to confirm it's a real picture.")
    print("[dds-video] => peek can use DDS; no Jetson helper needed.")
else:
    print("[dds-video] No frame over DDS. videohub may not expose the standard video API on G1;")
    print("[dds-video] next we'd check whether videohub serves an RTSP/HTTP port instead.")
    sys.exit(2)
