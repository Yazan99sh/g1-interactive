"""Read/write Dialogflow CX settings + run a live detectIntent test for the panel.

The Dialogflow tab lets you turn the "answer from Dialogflow first, fall back to the
LLM" behaviour on/off, point at the agent, set the confidence threshold, and fire a
test query straight at the agent to confirm it's wired. Values are written to ``.env``
and applied on the next pipeline restart.
"""
from __future__ import annotations

import os

from . import env_file, paths

_TRUE = ("1", "true", "yes", "on")
CONF_MIN, CONF_MAX = 0.0, 1.0


def _get(key: str, default: str = "") -> str:
    v = env_file.get(paths.ENV_FILE, key)
    return v if v not in (None, "") else default


def _get_bool(key: str, default: bool) -> bool:
    raw = env_file.get(paths.ENV_FILE, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE


def _get_float(key: str, default: float) -> float:
    try:
        return float(env_file.get(paths.ENV_FILE, key))
    except (TypeError, ValueError):
        return default


def get_config() -> dict:
    return {
        "enabled": _get_bool("DIALOGFLOW_ENABLED", False),
        "project": _get("DIALOGFLOW_PROJECT", "nova-1-474411"),
        "location": _get("DIALOGFLOW_LOCATION", "asia-south1"),
        "agent_id": _get("DIALOGFLOW_AGENT_ID", "acada473-7777-4ae3-a27e-c0218644aaf5"),
        "confidence": _get_float("DIALOGFLOW_CONFIDENCE", 0.6),
        "key_path": _get("DIALOGFLOW_KEY_PATH", ""),
    }


def set_config(enabled=None, confidence=None, project=None, location=None,
               agent_id=None, key_path=None) -> bool:
    """Write any provided settings to .env. Returns True iff something was written."""
    updates: dict[str, str] = {}
    if enabled is not None:
        updates["DIALOGFLOW_ENABLED"] = "true" if enabled else "false"
    if confidence is not None:
        try:
            c = float(confidence)
            if CONF_MIN <= c <= CONF_MAX:
                updates["DIALOGFLOW_CONFIDENCE"] = str(c)
        except (TypeError, ValueError):
            pass
    for key, val in (("DIALOGFLOW_PROJECT", project), ("DIALOGFLOW_LOCATION", location),
                     ("DIALOGFLOW_AGENT_ID", agent_id), ("DIALOGFLOW_KEY_PATH", key_path)):
        if val is not None:
            updates[key] = str(val).strip()
    if updates:
        env_file.update(paths.ENV_FILE, updates)
    return bool(updates)


def test_query(text: str) -> dict:
    """Send one detectIntent to the configured agent and report the result.

    Best-effort: needs google-cloud-dialogflow-cx on the panel host + a key. Returns a
    dict the Dialogflow tab renders; never raises.
    """
    if not text or not text.strip():
        return {"ok": False, "detail": "empty query"}
    cfg = get_config()
    project, location, agent = cfg["project"], cfg["location"], cfg["agent_id"]
    if not (project and location and agent):
        return {"ok": False, "detail": "project / location / agent not fully set"}
    try:
        from google.cloud import dialogflowcx_v3 as cx
    except Exception:
        return {"ok": False, "detail": "google-cloud-dialogflow-cx is not installed on the panel host"}

    import uuid

    endpoint = ("dialogflow.googleapis.com" if location == "global"
                else f"{location}-dialogflow.googleapis.com")
    try:
        creds = None
        if cfg["key_path"]:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_file(
                os.path.expanduser(cfg["key_path"]))
        client = cx.SessionsClient(client_options={"api_endpoint": endpoint}, credentials=creds)
        lang = "ar" if any("؀" <= ch <= "ۿ" for ch in text) else "en"
        session = (f"projects/{project}/locations/{location}/agents/{agent}"
                   f"/sessions/panel-{uuid.uuid4().hex}")
        req = cx.DetectIntentRequest(
            session=session,
            query_input=cx.QueryInput(text=cx.TextInput(text=text), language_code=lang),
        )
        qr = client.detect_intent(request=req).query_result
        match = qr.match
        mtype = cx.Match.MatchType(match.match_type).name if match else "NONE"
        conf = round(match.confidence, 2) if match else 0.0
        intent = match.intent.display_name if match and match.intent else ""
        answer = " ".join(t.strip() for m in qr.response_messages if m.text
                          for t in m.text.text if t and t.strip()).strip()
        return {"ok": True, "lang": lang, "match_type": mtype,
                "intent": intent, "confidence": conf, "answer": answer}
    except Exception as e:
        return {"ok": False, "detail": str(e)[:300]}
