"""Audio output sinks.

An ``AudioSink`` plays PCM16 mono audio. There are two implementations:

* ``HostSpeaker`` — plays through the PC's speakers via ``sounddevice`` (great for
  developing the whole pipeline with no robot attached).
* ``robot.speaker.G1Speaker`` — streams the same PCM to the G1's onboard speaker
  over DDS (the real deployment).

The pipeline only ever talks to this interface, so the robot vs host choice is a
one-line swap in ``main.py`` driven by ``AUDIO_SINK``.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from app.logging_setup import get_logger, log_exception

log = get_logger("audio.sink")


class AudioSink(ABC):
    @abstractmethod
    async def play(self, pcm: bytes, sample_rate: int) -> None:
        """Play a PCM16 mono buffer. Returns when playback finishes."""

    def stop(self) -> None:
        """Interrupt any in-progress playback (barge-in)."""

    async def close(self) -> None:
        """Release resources."""


class HostSpeaker(AudioSink):
    """Play audio on the local machine's speakers (development / no-robot mode)."""

    def __init__(self) -> None:
        self._sd = None
        self._stop = False

    def _ensure(self):
        if self._sd is None:
            import sounddevice as sd  # imported lazily so the app runs without PortAudio
            self._sd = sd
        return self._sd

    async def play(self, pcm: bytes, sample_rate: int) -> None:
        if not pcm:
            return
        self._stop = False
        try:
            import numpy as np
            sd = self._ensure()
            samples = np.frombuffer(pcm, dtype=np.int16)
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self._blocking_play(sd, samples, sample_rate)
            )
        except Exception:
            log_exception(log, "Host speaker playback failed")

    def _blocking_play(self, sd, samples, sample_rate: int) -> None:
        sd.play(samples, samplerate=sample_rate)
        sd.wait()

    def stop(self) -> None:
        self._stop = True
        if self._sd is not None:
            try:
                self._sd.stop()
            except Exception:
                pass

    async def close(self) -> None:
        self.stop()
