"""Small PCM / WAV helpers shared across the audio path.

All speech audio in this app is **16-bit signed little-endian PCM, mono**. The
only thing that varies is the sample rate, so these helpers deal in raw PCM
``bytes`` plus an integer sample-rate.
"""
from __future__ import annotations

import io
import wave

import numpy as np

SAMPLE_WIDTH = 2  # bytes per sample (PCM16)
CHANNELS = 1


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM16 mono into an in-memory WAV file (for HTTP upload)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_bytes_to_pcm(wav_bytes: bytes) -> tuple[bytes, int]:
    """Read a WAV file (bytes) → (pcm16_mono, sample_rate). Downmixes if needed."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != SAMPLE_WIDTH:
        # Convert to int16.
        arr = np.frombuffer(frames, dtype={1: np.uint8, 4: np.int32}.get(width, np.int16))
        arr = (arr.astype(np.float32) / (2 ** (8 * width - 1))) * 32767.0
        frames = arr.astype(np.int16).tobytes()
    if channels > 1:
        arr = np.frombuffer(frames, dtype=np.int16).reshape(-1, channels)
        frames = arr.mean(axis=1).astype(np.int16).tobytes()
    return frames, rate


def resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample PCM16 mono from ``src_rate`` to ``dst_rate`` (linear interpolation).

    Good enough for speech; avoids a scipy dependency. No-op when rates match.
    """
    if src_rate == dst_rate or not pcm:
        return pcm
    src = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if src.size == 0:
        return pcm
    duration = src.size / float(src_rate)
    dst_len = max(1, int(round(duration * dst_rate)))
    src_idx = np.linspace(0.0, src.size - 1, num=dst_len)
    dst = np.interp(src_idx, np.arange(src.size), src)
    return dst.astype(np.int16).tobytes()


def rms(pcm: bytes) -> float:
    """Root-mean-square amplitude of a PCM16 chunk (0..32767). Used for VAD."""
    if len(pcm) < 2:
        return 0.0
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)))


def pcm_duration_ms(pcm: bytes, sample_rate: int) -> float:
    return (len(pcm) / SAMPLE_WIDTH) / sample_rate * 1000.0
