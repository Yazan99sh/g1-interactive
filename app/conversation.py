"""Conversation state + system-prompt construction.

Holds the rolling dialogue history and builds the message list sent to the LLM,
injecting the persona, the current language, and any retrieved knowledge-base
context. The persona instructs the model to prepend an ``[EMOTION:x]`` tag, which
the pipeline strips and turns into an arm gesture + LED colour before speaking.
"""
from __future__ import annotations

from app.logging_setup import get_logger
from app.state import ChatMessage, Language
from config import settings

log = get_logger("app.conversation")

# Built-in fallback used only if prompts/persona.md is missing/empty. The live
# instructions are normally read from that file so the control panel can edit the
# robot's personality without code changes.
DEFAULT_PERSONA = """\
You are an interactive humanoid robot built by Altkamul, greeting and talking with \
visitors in person. You are friendly, warm, brief, and a little playful.

CRITICAL OUTPUT RULES (your words are spoken aloud through a speaker):
- Keep replies SHORT: 1-3 spoken sentences. No markdown, no bullet lists, no emojis, \
no code, no URLs — they sound wrong when spoken.
- Begin EVERY reply with exactly one emotion tag from this set, matching your tone: \
[EMOTION:happy] [EMOTION:excited] [EMOTION:curious] [EMOTION:thoughtful] \
[EMOTION:surprised] [EMOTION:playful] [EMOTION:neutral]. The tag is removed before \
speaking and is used to choose your arm gesture — so pick one that fits.
- Reply in the SAME language the user spoke. If they speak Arabic, answer in Arabic; \
if English, answer in English.
- If the provided KNOWLEDGE is relevant to the question, use it and stay accurate to it. \
If you don't know, say so briefly and offer to help with something else.
- You are physically present as a robot; you can wave and gesture while you talk, but you \
do not walk around. Don't claim abilities you don't have.
"""

PERSONA_FILE = "persona.md"


def load_persona() -> str:
    """Read the editable persona/instructions from prompts/persona.md.

    Falls back to ``DEFAULT_PERSONA`` if the file is missing or empty so the robot
    always has a working personality even on a fresh checkout.
    """
    try:
        path = settings.PROMPTS_DIR / PERSONA_FILE
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
            log.warning("%s is empty — using built-in default persona.", path)
        else:
            log.info("%s not found — using built-in default persona.", path)
    except Exception:
        log.warning("Could not read persona file — using built-in default.", exc_info=True)
    return DEFAULT_PERSONA


class ConversationManager:
    def __init__(self, max_turns: int = 12) -> None:
        self.history: list[ChatMessage] = []
        self.language: Language = Language.ENGLISH
        self.max_turns = max_turns
        self.persona = load_persona()

    def reload_persona(self) -> None:
        """Re-read prompts/persona.md (e.g. after the control panel edits it)."""
        self.persona = load_persona()

    def reset(self) -> None:
        self.history.clear()
        self.language = Language.ENGLISH

    def set_language(self, language: Language) -> None:
        if language and language != self.language:
            log.info("Conversation language -> %s", language.display_name)
            self.language = language

    def add_user(self, text: str) -> None:
        self.history.append(ChatMessage(role="user", content=text))
        self._trim()

    def add_assistant(self, text: str) -> None:
        self.history.append(ChatMessage(role="assistant", content=text))
        self._trim()

    def _trim(self) -> None:
        # Keep the last max_turns*2 messages (user+assistant pairs).
        limit = self.max_turns * 2
        if len(self.history) > limit:
            self.history = self.history[-limit:]

    def _system_prompt(self, kb_context: str) -> str:
        lang_line = (
            "\nThe user is currently speaking ARABIC — reply in Arabic."
            if self.language is Language.ARABIC
            else "\nThe user is currently speaking ENGLISH — reply in English."
        )
        kb_block = f"\n\nKNOWLEDGE (use if relevant):\n{kb_context}" if kb_context else ""
        return self.persona + lang_line + kb_block

    def build_messages(self, user_text: str, kb_context: str = "") -> list[ChatMessage]:
        """System prompt + recent history + this user turn (history already updated)."""
        return [ChatMessage(role="system", content=self._system_prompt(kb_context)), *self.history]
