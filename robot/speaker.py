"""G1 onboard speaker — an ``AudioSink`` over the verified G1 ``AudioClient``.

Source-verified against ``unitree_sdk2py/g1/audio/g1_audio_client.py`` + the C++
``g1_audio_client_example.cpp``:

* ``PlayStream(app_name: str, stream_id: str, pcm_data: bytes)`` — pass **bytes**
  (the client does ``list(pcm_data)`` internally; do NOT pre-wrap as list[int]).
* PCM must be **16000 Hz, mono, 16-bit signed LE, raw (no WAV header)** — exactly
  what we request from ElevenLabs (``output_format=pcm_16000``), so no resampling.
* Chunk ≤ **96000 bytes** (3 s); reuse ONE ``stream_id`` (ms-timestamp str) per
  utterance; sleep ~chunk-duration between chunks to avoid buffer overrun.
* ``PlayStop(app_name)`` interrupts (same app_name). ``SetVolume`` 0–100,
  ``LedControl(R,G,B)`` 0–255. Audio needs **no** locomotion/arm FSM.
"""
from __future__ import annotations

import asyncio
import threading
import time

from app.logging_setup import get_logger, log_exception
from audio.sink import AudioSink
from config import settings

log = get_logger("robot.speaker")

APP_NAME = "voicepipe"        # stable; PlayStop must reuse it
CHUNK_MAX = 96000             # bytes = 3.0 s @ 16k/16-bit/mono
STREAM_HZ = 16000
BYTES_PER_S = STREAM_HZ * 2   # mono s16le


class G1Speaker(AudioSink):
    def __init__(self) -> None:
        # Imported here so the app still runs (HostSpeaker fallback) without the SDK.
        from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

        self._client = AudioClient()
        self._client.SetTimeout(10.0)
        self._client.Init()
        try:
            self._client.SetVolume(max(0, min(100, settings.ROBOT_SPEAKER_VOLUME)))
        except Exception:
            log_exception(log, "SetVolume failed (non-fatal)")
        self._stop_flag = False
        self._rpc_lock = threading.Lock()  # AudioClient isn't thread-safe; serialise RPCs
        self._last_led: tuple[int, int, int] | None = None
        self._tail_drain_s = max(0.0, settings.ROBOT_SPEAKER_TAIL_DRAIN_MS / 1000.0)
        log.info("G1Speaker ready (volume=%d).", settings.ROBOT_SPEAKER_VOLUME)

    def set_led(self, r: int, g: int, b: int) -> None:
        """Set the head LED (0-255). De-duped so we don't spam identical RPCs."""
        color = (max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b))))
        if color == self._last_led:
            return
        self._last_led = color
        try:
            with self._rpc_lock:
                self._client.LedControl(*color)
        except Exception:
            log_exception(log, "LedControl failed (non-fatal)")

    # ---- AudioSink ----
    async def play(self, pcm: bytes, sample_rate: int) -> None:
        if not pcm:
            return
        self._stop_flag = False
        pcm = self._ensure_16k(pcm, sample_rate)
        await asyncio.get_running_loop().run_in_executor(None, self._play_blocking, pcm)

    def stop(self) -> None:
        self._stop_flag = True  # set before the lock so the feed loop exits next iteration
        try:
            with self._rpc_lock:
                self._client.PlayStop(APP_NAME)
        except Exception:
            log_exception(log, "PlayStop failed")

    async def close(self) -> None:
        self.stop()
        try:
            with self._rpc_lock:
                self._client.LedControl(0, 0, 0)
        except Exception:
            pass

    # ---- internals ----
    def _play_blocking(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            pcm = pcm[:-1]  # never split a 16-bit sample
        stream_id = str(int(time.time() * 1000))
        # NB: the head LED is now driven by pipeline STATE (set_led), not here, so it
        # can show listening/thinking/speaking — not just on/off during playback.
        #
        # We feed each chunk in ~0.9× its real duration so the robot's buffer never
        # underruns mid-utterance. That means by the end of the loop we've run AHEAD of
        # real playback by ~0.1× the total — the robot is STILL emitting buffered audio.
        # For one utterance that's invisible, but in chunked/streaming mode the NEXT
        # piece's play() would start PlayStream over this still-audible tail → the robot
        # talks over itself. So we track the lead we built up and drain it (sleep the
        # remainder + a small transport margin) before returning, so play() only returns
        # once this piece has actually finished. Barge-in (stop) skips the drain.
        fed_s = 0.0
        slept_s = 0.0
        try:
            for off in range(0, len(pcm), CHUNK_MAX):
                if self._stop_flag:
                    break
                chunk = pcm[off:off + CHUNK_MAX]
                with self._rpc_lock:  # lock the RPC, but never hold it across the sleep
                    self._client.PlayStream(APP_NAME, stream_id, chunk)
                dur = len(chunk) / BYTES_PER_S
                fed_s += dur
                nap = dur * 0.9  # 0.9× leaves a small underrun margin within the utterance
                slept_s += nap
                time.sleep(nap)
            # Drain the ~0.1× lead we built up (plus transport margin) so the buffered
            # tail finishes before we hand back — otherwise the next piece overlaps it.
            if not self._stop_flag:
                remaining = fed_s - slept_s + self._tail_drain_s
                if remaining > 0:
                    time.sleep(remaining)
        except Exception:
            log_exception(log, "PlayStream failed")

    @staticmethod
    def _ensure_16k(pcm: bytes, in_rate: int, in_ch: int = 1, in_width: int = 2) -> bytes:
        """No-op for our pcm_16000 path; resamples only if a stray rate appears."""
        if in_rate == STREAM_HZ and in_ch == 1 and in_width == 2:
            return pcm
        import audioop  # lazy (deprecated in py3.13; our default path never hits this)

        if in_width != 2:
            pcm = audioop.lin2lin(pcm, in_width, 2)
        if in_ch == 2:
            pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        if in_rate != STREAM_HZ:
            pcm, _ = audioop.ratecv(pcm, 2, 1, in_rate, STREAM_HZ, None)
        return pcm
