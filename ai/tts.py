"""Text-to-speech via ElevenLabs Flash v2.5.

Key choice: we request **raw PCM** (``output_format=pcm_16000``) instead of MP3 so
the bytes can go straight to the G1 speaker (and to ``sounddevice`` for host
testing) with no decoding step. The sample rate is parsed from the format string.

If your ElevenLabs plan cannot output PCM (it needs a paid tier), set
``TTS_OUTPUT_FORMAT=mp3_44100_128`` in .env; ``decode_to_pcm`` will transparently
decode it **iff** the optional ``miniaudio`` package is installed, otherwise it
raises a clear error telling you to switch to PCM or install a decoder.
"""
from __future__ import annotations

from typing import AsyncIterator, Optional

import httpx

from app.logging_setup import get_logger, log_exception
from app.state import Language
from config import settings

log = get_logger("ai.tts")

BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"


def format_sample_rate(output_format: str) -> int:
    """pcm_16000 -> 16000, mp3_44100_128 -> 44100, etc."""
    parts = output_format.split("_")
    for p in parts[1:]:
        if p.isdigit():
            return int(p)
    return 16000


def _is_pcm(output_format: str) -> bool:
    return output_format.lower().startswith("pcm")


def decode_to_pcm(audio: bytes, output_format: str) -> bytes:
    """Return PCM16 mono bytes. PCM passes through; MP3 needs optional miniaudio."""
    if _is_pcm(output_format):
        return audio
    try:
        import miniaudio  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "TTS_OUTPUT_FORMAT is MP3 but no decoder is installed. "
            "Set TTS_OUTPUT_FORMAT=pcm_16000 (recommended) or `pip install miniaudio`."
        ) from exc
    decoded = miniaudio.decode(
        audio, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
        sample_rate=format_sample_rate(output_format),
    )
    return decoded.samples.tobytes()


class ElevenLabsTTS:
    def __init__(self, http: httpx.AsyncClient, api_key: Optional[str] = None) -> None:
        self.http = http
        self.api_key = api_key or settings.ELEVENLABS_API_KEY
        self.model = settings.ELEVENLABS_MODEL
        self.output_format = settings.TTS_OUTPUT_FORMAT
        self.sample_rate = format_sample_rate(self.output_format)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def voice_for(self, language: Language) -> str:
        return (
            settings.ELEVENLABS_ARABIC_VOICE_ID
            if language is Language.ARABIC
            else settings.ELEVENLABS_VOICE_ID
        )

    def _body(self, text: str) -> dict:
        return {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": settings.TTS_STABILITY,
                "similarity_boost": settings.TTS_SIMILARITY,
                "speed": settings.TTS_SPEED,
            },
        }

    def _headers(self) -> dict:
        return {"xi-api-key": self.api_key, "Content-Type": "application/json"}

    async def synthesize(self, text: str, language: Language = Language.ENGLISH,
                         voice_id: Optional[str] = None) -> tuple[bytes, int]:
        """One-shot synth → (pcm16_mono_bytes, sample_rate)."""
        voice = voice_id or self.voice_for(language)
        url = f"{BASE_URL}/{voice}"
        params = {"output_format": self.output_format}
        try:
            resp = await self.http.post(
                url, headers=self._headers(), params=params, json=self._body(text), timeout=30.0
            )
        except Exception:
            log_exception(log, "TTS request failed (network)")
            return b"", self.sample_rate
        if resp.status_code != 200:
            log.error("TTS API error %s: %s", resp.status_code, resp.text[:300])
            return b"", self.sample_rate
        pcm = decode_to_pcm(resp.content, self.output_format)
        log.info("TTS ok: voice=%s chars=%d -> %d pcm bytes @ %dHz",
                 voice, len(text), len(pcm), self.sample_rate)
        return pcm, self.sample_rate

    async def stream(self, text: str, language: Language = Language.ENGLISH,
                     voice_id: Optional[str] = None) -> AsyncIterator[bytes]:
        """Stream PCM chunks as they are generated (lower latency to first audio).

        Only valid for PCM output formats (raw, chunk-safe). For MP3, use
        ``synthesize`` + ``decode_to_pcm`` instead.
        """
        if not _is_pcm(self.output_format):
            pcm, _ = await self.synthesize(text, language, voice_id)
            if pcm:
                yield pcm
            return
        voice = voice_id or self.voice_for(language)
        url = f"{BASE_URL}/{voice}/stream"
        params = {"output_format": self.output_format}
        try:
            async with self.http.stream(
                "POST", url, headers=self._headers(), params=params,
                json=self._body(text), timeout=60.0,
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread())[:300]
                    log.error("TTS stream error %s: %s", resp.status_code, body)
                    return
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk
        except Exception:
            log_exception(log, "TTS stream failed")
