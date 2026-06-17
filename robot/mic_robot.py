"""G1 onboard microphone — a ``MicSource`` over the robot's raw-PCM UDP multicast.

⚠️ EXPERIMENTAL / U6-UNVERIFIED. The G1 ``AudioClient`` is output-only (no mic
getter). The verified raw-mic mechanism (from Unitree's C++ audio example) is a
**plain UDP multicast** (NOT DDS): group ``239.168.123.161`` port ``5555``, payload
= raw PCM **int16, 16000 Hz, mono, s16le** (~160 ms/datagram). Whether this G1
"Plus"/videohub_pc4 variant actually emits it is unconfirmed — so this stays
behind ``MIC_SOURCE=robot`` and the default is the host USB mic.

If no packets arrive, ``read_chunk`` returns short silence buffers so the loop just
sees silence (→ no-speech turns) instead of hanging.
"""
from __future__ import annotations

import asyncio
import queue
import socket
import struct
import threading

from app.logging_setup import get_logger, log_exception
from audio.mic import MicSource
from config import settings

log = get_logger("robot.mic")


class G1Mic(MicSource):
    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self.group = settings.ROBOT_MIC_GROUP
        self.port = settings.ROBOT_MIC_PORT
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=256)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._silence = bytes(int(sample_rate * 0.1) * 2)  # 100 ms of silence

    async def open(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self.port))
        # Join on the robot-facing NIC, not whatever the routing table picks (which
        # on a multi-homed host is usually the internet/Wi-Fi route, so no packets).
        iface_ip = settings.ROBOT_MIC_IFACE_IP.strip()
        if iface_ip:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
            mreq = struct.pack("4s4s", socket.inet_aton(self.group), socket.inet_aton(iface_ip))
            log.info("G1 robot mic joining %s via %s.", self.group, iface_ip)
        else:
            mreq = struct.pack("4s4s", socket.inet_aton(self.group), socket.inet_aton("0.0.0.0"))
            log.warning("G1 robot mic joining %s with INADDR_ANY; set ROBOT_MIC_IFACE_IP "
                        "to the host's 192.168.123.x address if no audio arrives.", self.group)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(0.5)
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        log.info("G1 robot mic listening on %s:%d (experimental).", self.group, self.port)

    def _reader(self) -> None:
        while self._running and self._sock is not None:
            try:
                data, _ = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._q.put_nowait(data)
            except queue.Full:
                pass

    async def read_chunk(self) -> bytes:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._q.get, True, 0.2)
        except queue.Empty:
            return self._silence  # advance VAD timing as silence

    def flush(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    async def close(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                log_exception(log, "closing robot mic socket")
            self._sock = None
