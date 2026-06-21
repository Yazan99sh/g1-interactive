"""Read/write web-search (Brave) settings + a live test for the panel's Web Search tab.

Lets you turn the "search the web when asked / for current info" behaviour on/off, set
how many results to use, and edit the spoken "let me search" announcement for each
language. A test box runs a real Brave query so you can confirm the key works. Values
are written to ``.env`` and applied on the next pipeline restart.
"""
from __future__ import annotations

from . import env_file, paths

_TRUE = ("1", "true", "yes", "on")
COUNT_MIN, COUNT_MAX = 1, 10


def _get(key: str, default: str = "") -> str:
    v = env_file.get(paths.ENV_FILE, key)
    return v if v not in (None, "") else default


def _get_bool(key: str, default: bool) -> bool:
    raw = env_file.get(paths.ENV_FILE, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE


def _get_int(key: str, default: int) -> int:
    try:
        return int(env_file.get(paths.ENV_FILE, key))
    except (TypeError, ValueError):
        return default


def get_config() -> dict:
    return {
        "enabled": _get_bool("WEB_SEARCH_ENABLED", True),
        "count": _get_int("WEB_SEARCH_COUNT", 5),
        "announce_en": _get("WEB_SEARCH_ANNOUNCE_EN", "Sure, let me look that up online."),
        "announce_ar": _get("WEB_SEARCH_ANNOUNCE_AR", "حسنًا، لحظة، خليني أبحث في الإنترنت."),
        "key_set": bool(_get("BRAVE_SEARCH_API_KEY", "")),
    }


def set_config(enabled=None, count=None, announce_en=None, announce_ar=None) -> bool:
    """Write any provided settings to .env. Returns True iff something changed."""
    updates: dict[str, str] = {}
    if enabled is not None:
        updates["WEB_SEARCH_ENABLED"] = "true" if enabled else "false"
    if count is not None:
        try:
            n = int(count)
            if COUNT_MIN <= n <= COUNT_MAX:
                updates["WEB_SEARCH_COUNT"] = str(n)
        except (TypeError, ValueError):
            pass
    if announce_en is not None and str(announce_en).strip():
        updates["WEB_SEARCH_ANNOUNCE_EN"] = str(announce_en).strip()
    if announce_ar is not None and str(announce_ar).strip():
        updates["WEB_SEARCH_ANNOUNCE_AR"] = str(announce_ar).strip()
    if updates:
        env_file.update(paths.ENV_FILE, updates)
    return bool(updates)


def test_query(text: str) -> dict:
    """Run one live Brave search and report the top results. Never raises."""
    if not text or not text.strip():
        return {"ok": False, "detail": "empty query"}
    key = _get("BRAVE_SEARCH_API_KEY", "")
    if not key:
        return {"ok": False, "detail": "BRAVE_SEARCH_API_KEY is not set (Environment tab)"}
    try:
        import httpx
    except Exception:
        return {"ok": False, "detail": "httpx not installed on the panel host"}
    is_ar = any("؀" <= ch <= "ۿ" for ch in text)
    params = {
        "q": text, "count": str(get_config()["count"]), "text_decorations": "false",
        "search_lang": "ar" if is_ar else "en", "safesearch": "moderate",
    }
    headers = {"X-Subscription-Token": key, "Accept": "application/json"}
    try:
        r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                      params=params, headers=headers, timeout=15.0)
    except Exception as e:
        return {"ok": False, "detail": f"request failed: {str(e)[:200]}"}
    if r.status_code != 200:
        return {"ok": False, "detail": f"Brave error {r.status_code}: {r.text[:160]}"}
    try:
        items = (r.json().get("web") or {}).get("results") or []
    except Exception:
        return {"ok": False, "detail": "could not parse Brave response"}
    results = [{"title": it.get("title", ""), "description": it.get("description", "")}
               for it in items[: get_config()["count"]]]
    return {"ok": True, "lang": "ar" if is_ar else "en", "count": len(results), "results": results}
