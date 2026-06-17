"""LLM 'thinking' stage.

OpenRouter is the primary gateway; OpenAI direct is the fallback. Both speak the
same OpenAI ``/chat/completions`` schema, so it's one code path over two base
URLs with an automatic fallback chain (same idea as ``super-star``'s AiEngine).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from app.logging_setup import get_logger, log_exception
from app.state import ChatMessage
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
        order = [openai, openrouter] if settings.LLM_BACKEND == "openai" else [openrouter, openai]
        return [ep for ep in order if ep.api_key]

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
                "max_tokens": settings.LLM_MAX_TOKENS,
            }
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
