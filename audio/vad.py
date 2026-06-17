"""Voice-activity segmentation.

Decides when a spoken utterance has ended from a stream of PCM chunks, using the
same simple, reliable RMS approach proven in the ``super-star`` voice pipeline:

* speech *starts* when a chunk's RMS rises above ``rms_threshold``
* the utterance *ends* after ``silence_duration_ms`` of trailing silence,
  but only once at least ``min_recording_ms`` of audio have been captured
* a hard ``max_recording_ms`` cap stops run-on recordings

Timing is derived from chunk durations (deterministic), not wall-clock.
"""
from __future__ import annotations

from dataclasses import dataclass

from .wav import pcm_duration_ms, rms


@dataclass
class SegmentResult:
    done: bool          # the utterance is complete; read ``segmenter.pcm``
    had_speech: bool    # any speech was detected at all
    last_rms: float     # RMS of the chunk just fed (for live meters / logging)


class UtteranceSegmenter:
    def __init__(
        self,
        sample_rate: int,
        rms_threshold: float,
        silence_duration_ms: int,
        min_recording_ms: int,
        max_recording_ms: int,
        no_speech_timeout_ms: int = 0,
    ) -> None:
        self.sample_rate = sample_rate
        self.rms_threshold = rms_threshold
        self.silence_duration_ms = silence_duration_ms
        self.min_recording_ms = min_recording_ms
        self.max_recording_ms = max_recording_ms
        # If >0 and no speech is heard within this window, end early (a "silent"
        # session). Lets standby cycle and the idle-after-N-silent counter advance
        # without waiting for the full max_recording_ms each time.
        self.no_speech_timeout_ms = no_speech_timeout_ms
        self._audio = bytearray()
        self._elapsed_ms = 0.0
        self._silence_ms = 0.0
        self._has_speech = False
        self.reset()

    def reset(self) -> None:
        self._audio = bytearray()
        self._elapsed_ms = 0.0
        self._silence_ms = 0.0
        self._has_speech = False

    def feed(self, chunk: bytes) -> SegmentResult:
        self._audio.extend(chunk)
        dur = pcm_duration_ms(chunk, self.sample_rate)
        self._elapsed_ms += dur
        level = rms(chunk)

        if level > self.rms_threshold:
            self._has_speech = True
            self._silence_ms = 0.0
        elif self._has_speech:
            self._silence_ms += dur

        done = False
        if (
            self._has_speech
            and self._elapsed_ms >= self.min_recording_ms
            and self._silence_ms >= self.silence_duration_ms
        ):
            done = True
        if self._elapsed_ms >= self.max_recording_ms:
            done = True
        if (
            self.no_speech_timeout_ms
            and not self._has_speech
            and self._elapsed_ms >= self.no_speech_timeout_ms
        ):
            done = True

        return SegmentResult(done=done, had_speech=self._has_speech, last_rms=level)

    @property
    def pcm(self) -> bytes:
        return bytes(self._audio)

    @property
    def elapsed_ms(self) -> float:
        return self._elapsed_ms
