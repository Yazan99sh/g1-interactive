"""Read/write which LLM + TTS *models* the pipeline uses, for the panel's Models tab.

Two independent choices, both written to ``.env`` and applied on the next restart:

* **LLM ("thinking")** — pick a curated provider+model *preset*. Writes ``LLM_BACKEND``
  plus the matching model key (``OPENROUTER_MODEL`` / ``OPENAI_LLM_MODEL`` /
  ``GEMINI_LLM_MODEL``). The other configured providers stay as automatic fallbacks.
* **TTS ("voice")** — pick an ElevenLabs model id (``ELEVENLABS_MODEL``).

Nothing here calls a provider; it only edits config. Add a new model by appending to
``LLM_PRESETS`` / ``TTS_MODELS`` below — the panel renders the lists from these.
"""
from __future__ import annotations

from . import env_file, paths

# Each LLM preset couples a provider/backend with a concrete model id and the .env key
# that must be set for it to actually work. `backend` matches an Endpoint name in
# ai/llm.py (openrouter | openai | gemini).
LLM_PRESETS = [
    {"id": "gemini-3.1-flash-lite", "backend": "gemini", "model": "gemini-3.1-flash-lite",
     "key": "GEMINI_API_KEY", "label": "Gemini 3.1 Flash-Lite (Google) — fast & cheap"},
    {"id": "openrouter-gpt-4o-mini", "backend": "openrouter", "model": "openai/gpt-4o-mini",
     "key": "OPENROUTER_API_KEY", "label": "GPT-4o mini · via OpenRouter (default)"},
    {"id": "openai-gpt-4o-mini", "backend": "openai", "model": "gpt-4o-mini",
     "key": "OPENAI_API_KEY", "label": "GPT-4o mini · OpenAI direct"},
    {"id": "openai-gpt-4o", "backend": "openai", "model": "gpt-4o",
     "key": "OPENAI_API_KEY", "label": "GPT-4o · OpenAI direct — higher quality"},
]

# Which .env key holds the model id for each backend (and the backend's default model).
_MODEL_KEY_FOR_BACKEND = {
    "openrouter": ("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
    "openai": ("OPENAI_LLM_MODEL", "gpt-4o-mini"),
    "gemini": ("GEMINI_LLM_MODEL", "gemini-3.1-flash-lite"),
}

# Curated ElevenLabs TTS models. `id` is the ElevenLabs model_id sent in the request.
TTS_MODELS = [
    {"id": "eleven_flash_v2_5", "label": "Flash v2.5 — ultra-low latency (default)",
     "note": "Fastest first audio (~75 ms model latency). Best for snappy back-and-forth; "
             "streams raw PCM straight to the robot."},
    {"id": "eleven_v3", "label": "Eleven v3 — most expressive",
     "note": "Highest quality / most emotional, 70+ languages, but higher latency (not "
             "real-time) — best for short set replies. Confirm your ElevenLabs plan supports "
             "it and your TTS_OUTPUT_FORMAT."},
    {"id": "eleven_turbo_v2_5", "label": "Turbo v2.5 — superseded by Flash v2.5",
     "note": "Legacy. ElevenLabs marks Turbo as deprecated and functionally equivalent to "
             "Flash v2.5 but with higher latency — prefer Flash v2.5. Kept only for parity "
             "with existing configs."},
    {"id": "eleven_multilingual_v2", "label": "Multilingual v2 — rich, 29 languages",
     "note": "High quality, higher latency than Flash/Turbo."},
]

# ElevenLabs model ids are lower-case alphanumerics with separators; this also guards
# against newline/`=` injection into the .env when writing a custom value.
_SAFE_MODEL_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")


def _get(key: str, default: str = "") -> str:
    v = env_file.get(paths.ENV_FILE, key)
    return v if v not in (None, "") else default


def find_preset(preset_id: str) -> dict | None:
    for p in LLM_PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def match_llm_preset(backend: str, model: str) -> str:
    """Return the preset id whose backend+model equals the given pair, else ``"custom"``."""
    backend = (backend or "").strip().lower()
    model = (model or "").strip()
    for p in LLM_PRESETS:
        if p["backend"] == backend and p["model"] == model:
            return p["id"]
    return "custom"


def current_llm() -> tuple[str, str]:
    """The (backend, model) currently in .env, defaulting per backend."""
    backend = _get("LLM_BACKEND", "openrouter").strip().lower()
    model_key, model_default = _MODEL_KEY_FOR_BACKEND.get(
        backend, _MODEL_KEY_FOR_BACKEND["openrouter"])
    return backend, _get(model_key, model_default)


def get_config() -> dict:
    backend, model = current_llm()
    preset_id = match_llm_preset(backend, model)
    presets = [{"id": p["id"], "label": p["label"], "backend": p["backend"],
                "model": p["model"], "key_name": p["key"], "key_set": bool(_get(p["key"]))}
               for p in LLM_PRESETS]
    # Surface a synthetic "custom" entry so the dropdown can show (and preserve) a
    # non-preset config set via the Environment tab.
    if preset_id == "custom":
        presets.append({"id": "custom", "label": f"Custom · {backend} / {model}",
                        "backend": backend, "model": model, "key_name": "", "key_set": True})

    tts_model = _get("ELEVENLABS_MODEL", "eleven_flash_v2_5").strip()
    known = {m["id"] for m in TTS_MODELS}
    tts_models = list(TTS_MODELS)
    is_custom = bool(tts_model) and tts_model not in known
    if is_custom:
        tts_models.append({"id": tts_model, "label": f"Custom · {tts_model}",
                           "note": "Custom model id set via the Environment tab."})

    return {
        "llm": {"backend": backend, "model": model, "preset": preset_id, "presets": presets},
        "tts": {"model": tts_model, "is_custom": is_custom, "models": tts_models},
    }


def set_config(llm_preset=None, tts_model=None) -> bool:
    """Apply a curated LLM preset and/or an ElevenLabs TTS model. Returns True iff
    something was written. Unknown / ``custom`` LLM values are ignored (config preserved)."""
    updates: dict[str, str] = {}
    if llm_preset is not None:
        p = find_preset(str(llm_preset).strip())
        if p:
            updates["LLM_BACKEND"] = p["backend"]
            updates[_MODEL_KEY_FOR_BACKEND[p["backend"]][0]] = p["model"]
    if tts_model is not None:
        m = str(tts_model).strip()
        if m and all(ch in _SAFE_MODEL_CHARS for ch in m):
            updates["ELEVENLABS_MODEL"] = m
    if updates:
        env_file.update(paths.ENV_FILE, updates)
    return bool(updates)
