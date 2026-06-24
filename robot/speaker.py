"""G1 onboard speaker — an ``AudioSink`` over the verified G1 ``AudioClient``.

Source-verified against ``unitree_sdk2py/g1/audio/g1_audio_client.py`` + the C++
``g1_audio_client_example.cpp``:

* ``PlayStream(app_name: str, stream_id: str, pcm_data: bytes)`` — pass **bytes**
  (the client does ``list(pcm_data)`` internally; do NOT pre-wrap as list[int]).
* PCM must be **16000 Hz, mono, 16-bit signed LE, raw (no WAV header)** — exactly
  what we request from ElevenLabs (``output_format=pcm_16000``), so no resampling.
* Chunk ≤ **96000 bytes** (3 s); reuse ONE ``stream_id`` (ms-timestamp str) per
  utterance. We feed chunks on an ABSOLUTE schedule anchored to playback start (keep the
  feed ~(1-pace) ahead of real time) so a slow DDS send eats the buffer cushion instead of
  adding delay — otherwise the robot's playback underruns and the voice stutters. Chunk
  size + pace are configurable (``ROBOT_SPEAKER_CHUNK_BYTES``/``_PACE``; default 1 s, 0.9).
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
CHUNK_MAX = 96000             # bytes = 3.0 s @ 16k/16-bit/mono (transport upper bound)
CHUNK_MIN = 3200              # bytes = 0.1 s — floor so we never thrash on tiny chunks
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
        # Chunk size: even (never split a 16-bit sample) and within [CHUNK_MIN, CHUNK_MAX].
        chunk = int(settings.ROBOT_SPEAKER_CHUNK_BYTES)
        chunk -= chunk % 2
        self._chunk_bytes = max(CHUNK_MIN, min(CHUNK_MAX, chunk))
        self._pace = max(0.5, min(1.0, settings.ROBOT_SPEAKER_PACE))
        log.info("G1Speaker ready (volume=%d, chunk=%dB, pace=%.2f).",
                 settings.ROBOT_SPEAKER_VOLUME, self._chunk_bytes, self._pace)

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
        # NB: the head LED is driven by pipeline STATE (set_led), not here.
        #
        # Pacing uses an ABSOLUTE schedule rather than a fixed nap after each send. We aim to
        # stay ``(1-pace)`` of the audio AHEAD of real-time playback as a buffer cushion.
        # ``PlayStream`` is itself slow over DDS/Wi-Fi (a chunk can take ~its own duration to
        # transmit); a fixed ``sleep(pace·dur)`` AFTER each send would add ON TOP of that
        # transport time, so wall-clock per chunk > the chunk's audio duration and the robot's
        # playback buffer underruns mid-utterance — the "voice drops then comes back" stutter.
        # Sleeping to an absolute deadline instead SUBTRACTS the send time from the wait (a
        # slow send just eats the cushion), so we never fall further behind than the link forces.
        #
        # Anchor the schedule to PLAYBACK START — the moment the first chunk has actually been
        # sent — NOT to loop entry: the robot can't emit a sound until it has the first chunk,
        # so true audio end ~= play_start + fed_s. The tail-drain waits out that buffered tail
        # so play() returns only after the audio truly finished; otherwise (chunked/streaming
        # mode) the next piece would PlayStream over this one. We also bound true-end below by
        # ``last_send_done + last_chunk_dur`` for the slow-link case where the buffer underran
        # and the last chunk can't have finished before it was even received. Barge-in skips it.
        play_start: float | None = None
        last_done = 0.0
        last_dur = 0.0
        fed_s = 0.0
        slow = 0
        n_chunks = (len(pcm) + self._chunk_bytes - 1) // self._chunk_bytes
        try:
            for off in range(0, len(pcm), self._chunk_bytes):
                if self._stop_flag:
                    break
                chunk = pcm[off:off + self._chunk_bytes]
                t0 = time.monotonic()
                with self._rpc_lock:  # lock the RPC, but never hold it across the sleep
                    self._client.PlayStream(APP_NAME, stream_id, chunk)
                last_done = time.monotonic()
                if play_start is None:
                    play_start = last_done  # robot can't play until it has the first chunk
                dur = len(chunk) / BYTES_PER_S
                last_dur = dur
                if (last_done - t0) > dur:
                    slow += 1  # this send took longer than the audio lasts — link is the limit
                fed_s += dur
                if self._stop_flag:
                    break
                # Sleep only until the absolute deadline; a slow PlayStream shrinks (or
                # zeroes) this wait instead of adding to it.
                nap = (play_start + self._pace * fed_s) - time.monotonic()
                if nap > 0:
                    time.sleep(nap)
            # Wait out the buffered tail so play() returns only after the audio truly ended.
            if not self._stop_flag and play_start is not None:
                true_end = max(play_start + fed_s, last_done + last_dur)
                nap = (true_end + self._tail_drain_s) - time.monotonic()
                if nap > 0:
                    time.sleep(nap)
            if slow:
                log.warning("Speaker: %d/%d chunk(s) sent slower than real-time — audio may "
                            "stutter (DDS over Wi-Fi is marginal; a wired link to the robot "
                            "helps most).", slow, n_chunks)
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
