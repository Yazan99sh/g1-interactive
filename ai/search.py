"""Web search via the Brave Search API (optional).

When enabled (``WEB_SEARCH_ENABLED`` + ``BRAVE_SEARCH_API_KEY``), the pipeline can pull
fresh web results and answer from them — for explicit "search / look it up" requests and
for questions that need current info (news, weather, prices, recent events). The robot
ANNOUNCES that it's searching first, in the conversation language.

This module is self-contained: a Brave REST client, a bilingual (EN+AR) intent matcher
that decides whether a turn needs a search (and extracts the query), and a formatter that
turns results into LLM context. ``search_query()`` is pure so it's unit-testable.
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from app.logging_setup import get_logger, log_exception
from app.state import Language
from config import settings

log = get_logger("ai.search")

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

# English: explicit "search" verbs + strong fresh/real-time markers (word-boundaried,
# matched on the lowercased text). Deliberately NOT bare "today/now/current" — too common.
_EN_TRIGGERS = [
    r"\bsearch\b", r"\bgoogle\b", r"\blook (?:it|that|this) up\b", r"\blook up\b",
    r"\bsearch (?:the )?(?:web|internet|online)\b", r"\bfind (?:out|online)\b",
    r"\b(?:on|search) the (?:web|internet)\b",
    r"\blatest\b", r"\bbreaking news\b", r"\bnews\b", r"\bweather\b", r"\bforecast\b",
    r"\bprice of\b", r"\bhow much (?:is|are|does)\b", r"\bstock price\b", r"\bwho won\b",
    r"\bright now\b", r"\bup[- ]?to[- ]?date\b", r"\brecently\b", r"\bthis (?:week|year)\b",
    r"\b202[6-9]\b",
]
# Arabic: explicit search + fresh markers (substring; Arabic glues clitics).
_AR_TRIGGERS = [
    "ابحث", "إبحث", "ابحثي", "دوّر لي", "دور لي", "جوجل", "ابحث في الإنترنت", "ابحث في الانترنت",
    "آخر الأخبار", "آخر أخبار", "آخر الاخبار", "أحدث", "احدث", "آخر التطورات", "آخر المستجدات",
    "الأخبار", "الاخبار", "الطقس", "سعر", "كم سعر", "مين فاز", "النتيجة", "بحث في النت",
]
# Leading verb to strip so the Brave query is just the topic ("search for X" -> "X").
_STRIP = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|hey\s+|robot[, ]+)*"
    r"(?:search(?: the (?:web|internet|online))?(?: for)?|look (?:it|that|this)? ?up|google|"
    r"find (?:online|out)|ابحث(?: في الإنترنت| في الانترنت| في| عن| لي)?|دوّر(?: لي)?|دور(?: لي)?)"
    r"\s*[:،,]?\s*",
    re.IGNORECASE,
)


def search_query(text: str) -> Optional[str]:
    """Return a web-search query if this utterance asks for a search / current info,
    else None. Pure (no network)."""
    if not text or not text.strip():
        return None
    t = text.strip()
    low = t.lower()
    hit = any(re.search(p, low) for p in _EN_TRIGGERS) or any(k in t for k in _AR_TRIGGERS)
    if not hit:
        return None
    q = _STRIP.sub("", t).strip(" \t?؟.!،,")
    return q or t


def to_context(query: str, results: list[dict]) -> str:
    """Format Brave results as compact LLM context (title + snippet, no URLs spoken)."""
    lines = [f'WEB SEARCH RESULTS for "{query}":']
    for i, r in enumerate(results, 1):
        desc = re.sub(r"\s+", " ", (r.get("description") or "")).strip()
        title = (r.get("title") or "").strip()
        lines.append(f"{i}. {title} — {desc}")
    return "\n".join(lines)


class WebSearchClient:
    """Brave Search REST client. Safe no-op when disabled / no key."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.api_key = settings.BRAVE_SEARCH_API_KEY
        self.enabled = settings.WEB_SEARCH_ENABLED and bool(self.api_key)
        self.count = max(1, min(10, settings.WEB_SEARCH_COUNT))
        if settings.WEB_SEARCH_ENABLED and not self.api_key:
            log.warning("WEB_SEARCH_ENABLED but BRAVE_SEARCH_API_KEY is not set — web search off.")

    async def search(self, query: str, language: Language) -> list[dict]:
        """Return up to ``count`` results [{title, description, url}], or [] on any error."""
        if not self.enabled or not query or not query.strip():
            return []
        params = {
            "q": query, "count": str(self.count), "text_decorations": "false",
            "search_lang": "ar" if language is Language.ARABIC else "en",
            "safesearch": "moderate",
        }
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        try:
            r = await self.http.get(BRAVE_URL, params=params, headers=headers, timeout=15.0)
        except Exception:
            log_exception(log, "Brave search request failed (network)")
            return []
        if r.status_code != 200:
            log.warning("Brave search error %s: %s", r.status_code, r.text[:200])
            return []
        try:
            results = (r.json().get("web") or {}).get("results") or []
        except Exception:
            log_exception(log, "Brave search parse failed")
            return []
        out = [
            {"title": it.get("title", ""), "description": it.get("description", ""),
             "url": it.get("url", "")}
            for it in results[: self.count]
        ]
        log.info("Brave search '%s' -> %d result(s)", query[:60], len(out))
        return out
