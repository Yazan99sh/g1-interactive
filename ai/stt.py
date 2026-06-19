"""Speech-to-text — OpenAI ``gpt-4o-transcribe`` (default) or Groq Whisper.

Mirrors ``super-star``'s OpenAiSttProvider: multipart upload of a WAV, JSON response,
language inferred from the transcript's script. Groq exposes an OpenAI-COMPATIBLE
transcription endpoint (whisper-large-v3-turbo) that is much faster and cheaper with
strong Arabic+English, so the same request code serves both — only the base URL, key
and model differ. ``make_transcriber()`` picks the backend from STT_BACKEND and falls
back to OpenAI if the selected backend's key is missing.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.logging_setup import get_logger, log_exception
from app.state import Transcription, detect_language
from audio.wav import pcm_to_wav_bytes
from config import settings

log = get_logger("ai.stt")

OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
BASE_URL = OPENAI_URL  # back-compat alias


class OpenAITranscriber:
    """OpenAI-compatible Whisper-style transcriber (OpenAI or Groq)."""

    def __init__(self, http: httpx.AsyncClient, api_key: Optional[str] = None,
                 model: Optional[str] = None, base_url: str = OPENAI_URL,
                 name: str = "openai") -> None:
        self.http = http
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model or settings.OPENAI_STT_MODEL
        self.base_url = base_url
        self.name = name

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def transcribe(self, pcm: bytes, sample_rate: int, language_hint: Optional[str] = None) -> Transcription:
        """Transcribe a PCM16 mono utterance. Returns a blank Transcription on error."""
        if not pcm:
            return Transcription(text="")
        wav = pcm_to_wav_bytes(pcm, sample_rate)
        data = {"model": self.model, "response_format": "json"}
        if language_hint:
            data["language"] = language_hint
            if language_hint == "ar":
                data["prompt"] = "هذا نص باللغة العربية."
        files = {"file": ("audio.wav", wav, "audio/wav")}
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            resp = await self.http.post(self.base_url, headers=headers, data=data, files=files, timeout=30.0)
        except Exception:
            log_exception(log, "STT request failed (network)")
            return Transcription(text="", error=True)

        if resp.status_code != 200:
            log.error("STT API error %s: %s", resp.status_code, resp.text[:300])
            return Transcription(text="", error=True)

        try:
            text = (resp.json().get("text") or "").strip()
        except Exception:
            log_exception(log, "STT response parse failed")
            return Transcription(text="", error=True)

        lang = detect_language(text)
        log.info("STT (%s/%s): '%s' [%s]", self.name, self.model, text, lang.value if lang else "?")
        return Transcription(text=text, language=lang)


def make_transcriber(http: httpx.AsyncClient) -> OpenAITranscriber:
    """Build the transcriber for STT_BACKEND, falling back to OpenAI if the selected
    backend's key is missing."""
    backend = settings.STT_BACKEND
    if backend == "groq":
        if settings.GROQ_API_KEY:
            log.info("STT backend: Groq (%s).", settings.GROQ_STT_MODEL)
            return OpenAITranscriber(http, api_key=settings.GROQ_API_KEY,
                                     model=settings.GROQ_STT_MODEL, base_url=GROQ_URL, name="groq")
        log.warning("STT_BACKEND=groq but GROQ_API_KEY is not set — falling back to OpenAI.")
    return OpenAITranscriber(http, name="openai")
