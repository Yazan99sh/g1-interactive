"""Manage the robot's brain (file-based memory) from the panel's Memory tab.

Two things:
* **Config** — long-term memory on/off, per-session host snapshots on/off, retention
  (visitor & fact TTLs), recall depth. Written to ``.env``, applied on the next restart.
* **Browser** — list/read/edit/delete the atomic memory files, browse session snapshots,
  a "forget visitors now" button, and a stats summary.

Reuses ``app.memory.Brain`` for all store operations (parsing, index rebuild, sweeps) so
the panel and the running robot agree on the format. Import is lazy so the panel still
starts if the app package isn't importable for some reason.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import env_file, paths

_TRUE = ("1", "true", "yes", "on")


def _brain_dir() -> Path:
    raw = (env_file.get(paths.ENV_FILE, "BRAIN_DIR") or "brain").strip() or "brain"
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (paths.PROJECT_DIR / p)


def _brain():
    """A Brain bound to the configured dir (lazy import of the app package)."""
    from app.memory import Brain
    return Brain(_brain_dir())


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


def stats() -> dict:
    try:
        return _brain().stats()
    except Exception:
        return {"total": 0, "by_type": {}, "expiring_soon": 0, "sessions": 0}


def get_config() -> dict:
    return {
        "long_term_enabled": _get_bool("LONG_TERM_MEMORY_ENABLED", False),
        "session_snapshots": _get_bool("MEMORY_SESSION_SNAPSHOTS", True),
        "visitor_ttl_days": _get_int("MEMORY_VISITOR_TTL_DAYS", 7),
        "fact_ttl_days": _get_int("MEMORY_FACT_TTL_DAYS", 90),
        "recall_k": _get_int("MEMORY_RECALL_K", 3),
        "stats": stats(),
    }


def set_config(long_term=None, session_snapshots=None, visitor_ttl_days=None,
               fact_ttl_days=None, recall_k=None) -> bool:
    """Write any provided settings to .env. Returns True iff something changed."""
    updates: dict[str, str] = {}
    if long_term is not None:
        updates["LONG_TERM_MEMORY_ENABLED"] = "true" if long_term else "false"
    if session_snapshots is not None:
        updates["MEMORY_SESSION_SNAPSHOTS"] = "true" if session_snapshots else "false"
    if visitor_ttl_days is not None:
        try:
            updates["MEMORY_VISITOR_TTL_DAYS"] = str(max(0, int(visitor_ttl_days)))
        except (TypeError, ValueError):
            pass
    if fact_ttl_days is not None:
        try:
            updates["MEMORY_FACT_TTL_DAYS"] = str(max(0, int(fact_ttl_days)))
        except (TypeError, ValueError):
            pass
    if recall_k is not None:
        try:
            updates["MEMORY_RECALL_K"] = str(max(1, min(10, int(recall_k))))
        except (TypeError, ValueError):
            pass
    if updates:
        env_file.update(paths.ENV_FILE, updates)
    return bool(updates)


# ---- memory file browser -------------------------------------------------
def list_memories() -> dict:
    """Lightweight listing of every atomic memory file (no bodies)."""
    try:
        records = _brain()._load_all()
    except Exception:
        return {"memories": []}
    items = [{
        "id": r.id, "type": r.type, "subject": r.subject, "salience": r.salience,
        "expiry": r.expiry, "tags": r.tags, "summary": r.description,
    } for r in sorted(records, key=lambda x: (-x.salience, x.subject))]
    return {"memories": items}


def _safe_id(rec_id: str) -> str:
    paths.safe_child(_brain_dir() / "memories", f"{rec_id}.md", (".md",))  # raises on traversal
    return rec_id


def read_memory(rec_id: str) -> dict:
    try:
        _safe_id(rec_id)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    path = _brain_dir() / "memories" / f"{rec_id}.md"
    if not path.exists():
        return {"ok": False, "detail": "not found"}
    return {"ok": True, "id": rec_id, "content": path.read_text(encoding="utf-8", errors="replace")}


def write_memory(rec_id: str, content: str) -> dict:
    """Overwrite a memory file's raw Markdown, then rebuild the index so recall stays
    consistent. Only allows editing files that already exist (no arbitrary creation)."""
    try:
        _safe_id(rec_id)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    path = _brain_dir() / "memories" / f"{rec_id}.md"
    if not path.exists():
        return {"ok": False, "detail": "not found"}
    brain = _brain()
    brain._atomic_write(path, content or "")
    brain._rebuild_index()
    return {"ok": True}


def delete_memory(rec_id: str) -> dict:
    try:
        _safe_id(rec_id)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    path = _brain_dir() / "memories" / f"{rec_id}.md"
    if path.exists():
        path.unlink()
    _brain()._rebuild_index()
    return {"ok": True}


def forget_visitors() -> dict:
    try:
        n = _brain().forget_visitors()
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "removed": n}


# ---- session snapshots ---------------------------------------------------
def list_sessions(limit: int = 50) -> dict:
    sdir = _brain_dir() / "sessions"
    if not sdir.exists():
        return {"sessions": []}
    out = []
    for p in sorted(sdir.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": data.get("id", p.stem), "ended_at": data.get("ended_at", ""),
                        "turn_count": data.get("turn_count", 0)})
        except Exception:
            out.append({"id": p.stem, "ended_at": "", "turn_count": 0})
    return {"sessions": out}


def read_session(session_id: str) -> dict:
    try:
        paths.safe_child(_brain_dir() / "sessions", f"{session_id}.json", (".json",))
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    path = _brain_dir() / "sessions" / f"{session_id}.json"
    if not path.exists():
        return {"ok": False, "detail": "not found"}
    try:
        return {"ok": True, "session": json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        return {"ok": False, "detail": "could not parse snapshot"}
