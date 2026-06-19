"""Microphone input.

A ``MicSource`` yields raw PCM16 mono chunks at ``SAMPLE_RATE``. Two sources:

* ``HostMic`` — a USB/built-in mic on the host PC via ``sounddevice`` (default;
  put the mic near the robot so people speak into it).
* ``robot.mic_robot.G1Mic`` — the G1's onboard mic over DDS, *if* the verified
  spec confirms it's available (finalised after the research workflow).

``record_utterance`` drives an :class:`~audio.vad.UtteranceSegmenter` with chunks
from any source and returns the captured speech.
"""
from __future__ import annotations

import asyncio
import queue
from abc import ABC, abstractmethod
from typing import Callable, Optional

from app.logging_setup import get_logger, log_exception
from audio.vad import UtteranceSegmenter
from audio.wav import resample_pcm

log = get_logger("audio.mic")

CHUNK_MS = 50  # 50 ms chunks → snappy metering, fine for 900 ms end-pointing


class MicSource(ABC):
    sample_rate: int

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def read_chunk(self) -> bytes:
        """Return the next PCM16 mono chunk (~CHUNK_MS of audio)."""

    def flush(self) -> None:
        """Discard any buffered audio (e.g. the robot's own voice captured during
        playback) so the next utterance starts clean. No-op by default."""

    @abstractmethod
    async def close(self) -> None: ...


class HostMic(MicSource):
    def __init__(self, sample_rate: int, device: Optional[str] = None) -> None:
        self.sample_rate = sample_rate
        self.device = device or None
        self.capture_rate = sample_rate  # may be raised in open() if the device needs it
        self.blocksize = int(sample_rate * CHUNK_MS / 1000)
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=64)
        self._stream = None
        self._silence = bytes(int(sample_rate * CHUNK_MS / 1000) * 2)  # one chunk of int16 silence

    @staticmethod
    def _resolve_input_device(sd, device):
        """Return a device that actually has an input channel. On many laptops the
        DEFAULT device PortAudio picks is not a capture device (0 input channels), so
        opening a mono stream fails with 'Invalid number of channels'. In that case
        fall back to the first input-capable device so the robot can still hear."""
        def n_in(dev) -> int:
            try:
                info = sd.query_devices(dev, "input") if dev is not None else sd.query_devices(kind="input")
                return int(info.get("max_input_channels", 0))
            except Exception:
                return 0

        if n_in(device) >= 1:
            return device
        try:
            for idx, d in enumerate(sd.query_devices()):
                if int(d.get("max_input_channels", 0)) >= 1:
                    log.warning("Input device %r has no input channels; using '%s' (index %d). "
                                "Set MIC_DEVICE to choose a specific mic.", device, d["name"], idx)
                    return idx
        except Exception:
            pass
        log.error("No audio input device with input channels found — plug in a mic or set MIC_DEVICE.")
        return device

    async def open(self) -> None:
        import sounddevice as sd  # lazy import

        device = self.device
        if device is not None and str(device).isdigit():
            device = int(device)

        # Pick a device that can actually capture (the default may have 0 input chans).
        device = self._resolve_input_device(sd, device)

        # Many mics only support 44.1/48 kHz, not 16 kHz. Capture at a rate the
        # device actually supports, then resample to SAMPLE_RATE in read_chunk.
        rate = self.sample_rate
        try:
            sd.check_input_settings(device=device, samplerate=rate, channels=1, dtype="int16")
        except Exception:
            try:
                info = sd.query_devices(device, "input")
                rate = int(info["default_samplerate"])
            except Exception:
                rate = 48000
            log.warning("Mic doesn't support %d Hz; capturing at %d Hz and resampling.",
                        self.sample_rate, rate)
        self.capture_rate = rate
        self.blocksize = int(rate * CHUNK_MS / 1000)

        def _callback(indata, _frames, _time, status):
            if status:
                log.debug("mic status: %s", status)
            try:
                self._q.put_nowait(bytes(indata))
            except queue.Full:
                pass  # drop if the consumer fell behind

        self._stream = sd.RawInputStream(
            samplerate=rate, blocksize=self.blocksize, device=device,
            dtype="int16", channels=1, callback=_callback,
        )
        self._stream.start()
        log.info("Host mic open: capture %d Hz -> %d Hz, %d-frame blocks, device=%s",
                 rate, self.sample_rate, self.blocksize, device if device is not None else "default")

    async def read_chunk(self) -> bytes:
        # Bounded get so the executor thread wakes periodically — lets the loop
        # re-check the stop flag and never hangs shutdown if the stream stalls.
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(None, self._q.get, True, 0.2)
        except queue.Empty:
            return self._silence
        if self.capture_rate != self.sample_rate:
            raw = resample_pcm(raw, self.capture_rate, self.sample_rate)
        return raw

    def flush(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    async def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


async def record_utterance(
    mic: MicSource,
    segmenter: UtteranceSegmenter,
    on_level: Optional[Callable[[float], None]] = None,
) -> tuple[bytes, bool]:
    """Capture one utterance: read chunks, feed the segmenter, stop when it ends.

    Returns (pcm, had_speech). ``on_level`` (optional) receives each chunk's RMS
    for live metering / logging.
    """
    segmenter.reset()
    mic.flush()  # drop audio buffered during the previous reply (anti self-trigger)
    try:
        while True:
            chunk = await mic.read_chunk()
            result = segmenter.feed(chunk)
            if on_level is not None:
                on_level(result.last_rms)
            if result.done:
                return segmenter.pcm, result.had_speech
    except Exception:
        log_exception(log, "record_utterance failed")
        return segmenter.pcm, False
