"""Speech-to-text via OpenAI ``gpt-4o-transcribe``.

Mirrors ``super-star``'s OpenAiSttProvider: multipart upload of a WAV, JSON
response, language inferred from the transcript's script (gpt-4o-transcribe only
supports ``response_format=json``, which carries no language field).
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.logging_setup import get_logger, log_exception
from app.state import Transcription, detect_language
from audio.wav import pcm_to_wav_bytes
from config import settings

log = get_logger("ai.stt")

BASE_URL = "https://api.openai.com/v1/audio/transcriptions"


class OpenAITranscriber:
    def __init__(self, http: httpx.AsyncClient, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.http = http
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model or settings.OPENAI_STT_MODEL

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
            resp = await self.http.post(BASE_URL, headers=headers, data=data, files=files, timeout=30.0)
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
        log.info("STT (%s): '%s' [%s]", self.model, text, lang.value if lang else "?")
        return Transcription(text=text, language=lang)
