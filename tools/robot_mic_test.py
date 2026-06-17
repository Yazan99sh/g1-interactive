"""Probe whether the G1 streams its onboard microphone over UDP multicast.

Listens on the robot's raw-audio multicast group for 5 seconds. If packets
arrive, the robot's mic is usable (set MIC_SOURCE=robot); if not, use a USB mic.

    python tools/robot_mic_test.py [iface_ip]
    # iface_ip = the host's 192.168.123.x address (default 192.168.123.100)
"""
import socket
import struct
import sys
import time

import numpy as np

GROUP = "239.168.123.161"
PORT = 5555
IFACE_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.123.100"

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", PORT))
s.setsockopt(
    socket.IPPROTO_IP,
    socket.IP_ADD_MEMBERSHIP,
    struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton(IFACE_IP)),
)
s.settimeout(5)

print(f"Listening 5s on {GROUP}:{PORT} via {IFACE_IP} - talk near the robot...")
n, loud, t = 0, 0.0, time.time()
while time.time() - t < 5:
    try:
        data, _ = s.recvfrom(65535)
    except socket.timeout:
        break
    n += 1
    a = np.frombuffer(data, dtype=np.int16).astype(float)
    if a.size:
        loud = max(loud, float(np.sqrt(np.mean(a * a))))

print(f"packets: {n}   peak loudness: {round(loud, 1)}")
print("ROBOT MIC WORKS - no USB mic needed" if n else "NO PACKETS - use a USB mic")
