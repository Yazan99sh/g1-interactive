"""Dialogflow CX "first answer" stage (optional).

When enabled, each turn is sent to a Dialogflow CX agent (``sessions.detectIntent``)
BEFORE the LLM. If CX matches a structured intent with confidence ≥ threshold and
returns text, that verbatim answer is spoken and the LLM is skipped. Otherwise — and
whenever the toggle is off, the agent is unreachable, or the lib/key is missing — the
caller falls back to the LLM. This mirrors ``super-star``'s DialogflowProvider:
"Dialog Flow first, the model if it has no answer."

Design notes:
* The ``google-cloud-dialogflow-cx`` library is imported LAZILY, so the app still runs
  if it isn't installed — the client just stays disabled and everything uses the LLM.
* ``detect_intent`` is a blocking gRPC call, so it runs in a thread-pool executor.
* A generative (Playbook) agent answers with an LLM refusal instead of our intents;
  we reject ``PLAYBOOK``/``NO_MATCH`` matches so such answers never get spoken (the
  import script also pins the agent to its intent flow — see tools/cx_import.py).
* One CX session per conversation (rotated on wake) so turns share context but a new
  visitor starts clean.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from app.logging_setup import get_logger, log_exception
from app.state import Language
from config import settings

log = get_logger("ai.dialogflow")

# Only speak deterministic INTENT matches. Everything else — NO_MATCH, generative
# PLAYBOOK / KNOWLEDGE_CONNECTOR answers, the welcome/negative fallbacks, events — is
# left to the LLM. (Allow-list, not a reject-list, so a reconfigured agent can't leak
# a generative answer.)
_ACCEPT_MATCH_TYPES = {"INTENT", "DIRECT_INTENT"}


class DialogflowClient:
    """Optional Dialogflow CX detectIntent client. Safe no-op when disabled/unavailable."""

    def __init__(self) -> None:
        self.enabled = settings.DIALOGFLOW_ENABLED
        self.confidence = settings.DIALOGFLOW_CONFIDENCE
        self.project = settings.DIALOGFLOW_PROJECT
        self.location = settings.DIALOGFLOW_LOCATION
        self.agent = settings.DIALOGFLOW_AGENT_ID
        self._cx = None
        self._client = None
        self._inited = False
        self._ok = False
        self._session_id = uuid.uuid4().hex

    def new_session(self) -> None:
        """Start a fresh CX session (call on each wake / new visitor)."""
        self._session_id = uuid.uuid4().hex

    @property
    def _endpoint(self) -> str:
        return ("dialogflow.googleapis.com" if self.location == "global"
                else f"{self.location}-dialogflow.googleapis.com")

    def _ensure(self) -> bool:
        """Lazily build the SessionsClient. Returns False (disabled) on any problem."""
        if not self.enabled:
            return False
        if self._inited:
            return self._ok
        self._inited = True
        if not (self.project and self.location and self.agent):
            log.warning("Dialogflow enabled but project/location/agent not fully set — disabling.")
            return False
        try:
            from google.cloud import dialogflowcx_v3 as cx
            creds = None
            if settings.DIALOGFLOW_KEY_PATH:
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(settings.DIALOGFLOW_KEY_PATH)
            self._cx = cx
            self._client = cx.SessionsClient(
                client_options={"api_endpoint": self._endpoint}, credentials=creds
            )
            self._ok = True
            log.info("Dialogflow CX ready (%s/%s/%s, conf≥%.2f).",
                     self.project, self.location, self.agent, self.confidence)
        except Exception:
            log_exception(log, "Dialogflow CX unavailable (lib/key/init) — using the LLM instead")
            self._ok = False
        return self._ok

    async def answer(self, text: str, language: Language) -> Optional[str]:
        """Return a confident CX answer for ``text``, or None to fall back to the LLM."""
        if not text or not text.strip() or not self.enabled:
            return None
        try:
            # All blocking work (first-time client init: key read + gRPC channel; then
            # detectIntent) runs off the event loop in one executor hop.
            return await asyncio.get_running_loop().run_in_executor(
                None, self._answer_blocking, text, language
            )
        except Exception:
            log_exception(log, "Dialogflow detectIntent failed — falling back to the LLM")
            return None

    def _answer_blocking(self, text: str, language: Language) -> Optional[str]:
        if not self._ensure():
            return None
        cx = self._cx
        lang = "ar" if language is Language.ARABIC else "en"
        session = (f"projects/{self.project}/locations/{self.location}"
                   f"/agents/{self.agent}/sessions/{self._session_id}")
        req = cx.DetectIntentRequest(
            session=session,
            query_input=cx.QueryInput(text=cx.TextInput(text=text), language_code=lang),
        )
        qr = self._client.detect_intent(request=req).query_result
        match = qr.match
        if not match:
            return None
        mtype = cx.Match.MatchType(match.match_type).name
        conf = match.confidence or 0.0
        if mtype not in _ACCEPT_MATCH_TYPES or conf < self.confidence:
            log.info("Dialogflow: no confident intent (type=%s conf=%.2f) — LLM fallback.", mtype, conf)
            return None
        parts = [t.strip() for m in qr.response_messages if m.text
                 for t in m.text.text if t and t.strip()]
        answer = " ".join(parts).strip()
        if not answer:
            return None
        name = match.intent.display_name if match.intent else "?"
        log.info("Dialogflow answered (intent=%s, conf=%.2f).", name, conf)
        return answer
