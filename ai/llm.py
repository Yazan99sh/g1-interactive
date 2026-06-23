"""LLM 'thinking' stage.

OpenRouter is the primary gateway; OpenAI direct is the fallback. Both speak the
same OpenAI ``/chat/completions`` schema, so it's one code path over two base
URLs with an automatic fallback chain (same idea as ``super-star``'s AiEngine).
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

from app.logging_setup import get_logger, log_exception
from app.state import ChatMessage, Language
from config import settings

log = get_logger("ai.llm")


@dataclass
class Endpoint:
    name: str
    url: str
    api_key: str
    model: str
    extra_headers: dict


class LLMEngine:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.endpoints = self._build_chain()
        if not self.endpoints:
            log.warning("No LLM endpoints configured (missing API keys).")

    def _build_chain(self) -> list[Endpoint]:
        openrouter = Endpoint(
            name="openrouter",
            url="https://openrouter.ai/api/v1/chat/completions",
            api_key=settings.OPENROUTER_API_KEY,
            model=settings.OPENROUTER_MODEL,
            extra_headers={
                "HTTP-Referer": "https://altkamul.local/g1-interactive",
                "X-Title": "G1 Interactive Robot",
            },
        )
        openai = Endpoint(
            name="openai",
            url="https://api.openai.com/v1/chat/completions",
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_LLM_MODEL,
            extra_headers={},
        )
        # Google Gemini speaks the OpenAI /chat/completions schema at this base URL,
        # so it slots into the same code path (Bearer auth, stream=true, [DONE]).
        gemini = Endpoint(
            name="gemini",
            url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            api_key=settings.GEMINI_API_KEY,
            model=settings.GEMINI_LLM_MODEL,
            extra_headers={},
        )
        # Put the selected backend first; the rest follow as automatic fallbacks (stable
        # order). Endpoints without an API key are dropped.
        chain = [openrouter, openai, gemini]
        chain.sort(key=lambda ep: 0 if ep.name == settings.LLM_BACKEND else 1)
        return [ep for ep in chain if ep.api_key]

    async def complete(self, messages: list[ChatMessage]) -> str:
        """Return the assistant reply text, trying each endpoint in turn.

        Returns "" if every endpoint fails (the pipeline then speaks a fallback line).
        """
        payload_messages = [{"role": m.role, "content": m.content} for m in messages]
        last_error: Optional[str] = None

        for ep in self.endpoints:
            payload = {
                "model": ep.model,
                "messages": payload_messages,
                "temperature": settings.LLM_TEMPERATURE,
            }
            if settings.LLM_MAX_TOKENS > 0:  # 0 = no cap (omit so nothing truncates)
                payload["max_tokens"] = settings.LLM_MAX_TOKENS
            headers = {"Authorization": f"Bearer {ep.api_key}", **ep.extra_headers}
            try:
                resp = await self.http.post(ep.url, headers=headers, json=payload, timeout=60.0)
            except Exception:
                log_exception(log, f"LLM request to {ep.name} failed (network)")
                last_error = f"{ep.name}: network"
                continue

            if resp.status_code != 200:
                log.warning("LLM %s error %s: %s", ep.name, resp.status_code, resp.text[:300])
                last_error = f"{ep.name}: {resp.status_code}"
                continue

            try:
                text = resp.json()["choices"][0]["message"]["content"] or ""
            except Exception:
                log_exception(log, f"LLM {ep.name} response parse failed")
                last_error = f"{ep.name}: parse"
                continue

            log.info("LLM ok via %s (%s): %d chars", ep.name, ep.model, len(text))
            return text.strip()

        log.error("All LLM endpoints failed. Last error: %s", last_error)
        return ""

    async def describe_image(self, jpeg: bytes, question: str, language: Language) -> str:
        """Look at a head-camera JPEG and answer the visitor's request in 1-2 spoken
        sentences (for the 'peek' feature). Uses the same endpoint chain; the model is
        ``VISION_MODEL`` if set, else the endpoint's own model (gpt-4o-mini supports
        vision). Returns "" if every endpoint fails — the caller speaks a fallback."""
        if not self.endpoints or not jpeg:
            return ""
        b64 = base64.b64encode(jpeg).decode("ascii")
        lang_line = ("Reply in ARABIC, and write any number as Arabic words, not digits."
                     if language is Language.ARABIC else "Reply in ENGLISH.")
        system = (
            "You are the eyes of a friendly humanoid robot greeting people in person. Look at "
            "the photo from your camera and answer the person's request about what you see in ONE "
            "or TWO short sentences, spoken aloud. " + lang_line + " Speak as if you are looking "
            "right now; do NOT say 'image' or 'photo' or describe pixels. Begin with exactly one "
            "[EMOTION:x] tag (happy/curious/surprised/playful/thoughtful/neutral)."
        )
        user_content = [
            {"type": "text", "text": question.strip() or "What do you see right now?"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
        payload_messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        last_error: Optional[str] = None
        for ep in self.endpoints:
            model = settings.VISION_MODEL or ep.model
            payload = {"model": model, "messages": payload_messages,
                       "temperature": settings.LLM_TEMPERATURE}
            headers = {"Authorization": f"Bearer {ep.api_key}", **ep.extra_headers}
            try:
                resp = await self.http.post(ep.url, headers=headers, json=payload, timeout=60.0)
            except Exception:
                log_exception(log, f"Vision request to {ep.name} failed (network)")
                last_error = f"{ep.name}: network"
                continue
            if resp.status_code != 200:
                log.warning("Vision %s error %s: %s", ep.name, resp.status_code, resp.text[:300])
                last_error = f"{ep.name}: {resp.status_code}"
                continue
            try:
                text = resp.json()["choices"][0]["message"]["content"] or ""
            except Exception:
                log_exception(log, f"Vision {ep.name} response parse failed")
                last_error = f"{ep.name}: parse"
                continue
            log.info("Vision ok via %s (%s): %d chars", ep.name, model, len(text))
            return text.strip()
        log.error("All vision endpoints failed. Last error: %s", last_error)
        return ""

    async def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        """Yield assistant text deltas as they arrive (OpenAI SSE ``stream=true``).

        Tries each endpoint in turn. Once an endpoint has produced any content it is
        committed (no failover mid-reply). If every endpoint fails it simply yields
        nothing — the caller is expected to fall back to ``complete()``.
        """
        payload_messages = [{"role": m.role, "content": m.content} for m in messages]
        for ep in self.endpoints:
            payload = {
                "model": ep.model,
                "messages": payload_messages,
                "temperature": settings.LLM_TEMPERATURE,
                "stream": True,
            }
            if settings.LLM_MAX_TOKENS > 0:  # 0 = no cap (omit so nothing truncates)
                payload["max_tokens"] = settings.LLM_MAX_TOKENS
            headers = {"Authorization": f"Bearer {ep.api_key}", **ep.extra_headers}
            got_any = False
            try:
                async with self.http.stream(
                    "POST", ep.url, headers=headers, json=payload, timeout=60.0
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread())[:300]
                        log.warning("LLM stream %s error %s: %s", ep.name, resp.status_code, body)
                        continue
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data)["choices"][0]["delta"].get("content")
                        except Exception:
                            continue
                        if delta:
                            got_any = True
                            yield delta
            except Exception:
                log_exception(log, f"LLM stream to {ep.name} failed (network)")
                continue
            if got_any:
                log.info("LLM stream ok via %s (%s)", ep.name, ep.model)
                return
        log.error("All LLM streaming endpoints failed.")
