"""Resolved filesystem paths for the control panel + path-safety helpers.

Everything is derived from this package's location so the panel is portable: it
manages whatever app lives in the parent directory, on any machine.
"""
from __future__ import annotations

from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PKG_DIR.parent  # the g1-interactive app root (parent of controlpanel/)

LOGS_DIR = PROJECT_DIR / "logs"
KNOWLEDGE_DIR = PROJECT_DIR / "knowledge"
PROMPTS_DIR = PROJECT_DIR / "prompts"
TOOLS_DIR = PROJECT_DIR / "tools"
SCRIPTS_DIR = PROJECT_DIR / "scripts"

ENV_FILE = PROJECT_DIR / ".env"
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
PERSONA_FILE = PROMPTS_DIR / "persona.md"
EVENTS_FILE = LOGS_DIR / "events.jsonl"
MAIN_PY = PROJECT_DIR / "main.py"

STATIC_DIR = PKG_DIR / "static"
STATE_DIR = PKG_DIR / "state"

LOG_FILES = {
    "g1": LOGS_DIR / "g1.log",
    "errors": LOGS_DIR / "errors.log",
    # Raw stdout+stderr of the pipeline process (subprocess mode) — shows crashes that
    # happen before logging is even set up (bad import, missing key, etc.).
    "pipeline": LOGS_DIR / "pipeline.out.log",
}


def ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


def safe_child(base: Path, name: str, allowed_suffixes: tuple[str, ...]) -> Path:
    """Resolve ``base/name`` and confirm it's a direct child of ``base`` with an
    allowed suffix. Rejects traversal, separators, hidden and nested names."""
    if not name or name != name.strip():
        raise ValueError("empty or padded name")
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise ValueError("illegal characters in name")
    target = (base / name).resolve()
    if target.parent != base.resolve():
        raise ValueError("name escapes its directory")
    if allowed_suffixes and target.suffix.lower() not in allowed_suffixes:
        raise ValueError(f"only {', '.join(allowed_suffixes)} allowed")
    return target
