"""The robot's brain — a persistent, file-based memory.

OpenClaw / Anthropic-memory-tool style: **Markdown is the source of truth**, a small
index file (``MEMORY.md``) is a cheap, *derived* list read first, and only the few
relevant memory files are loaded per turn — so the robot brings just the slice it
needs into the LLM context instead of one giant linear blob (token-efficient).

Two independent behaviours, gated by ``config``:

* **Session snapshots** (``MEMORY_SESSION_SNAPSHOTS``) — every finished session is written
  to ``brain/sessions/<id>.json`` + appended to ``brain/logs/<date>.md`` on the host. This
  is the "save a copy after each session" requirement; it works even with long-term off.
* **Long-term memory** (``LONG_TERM_MEMORY_ENABLED``) — at session end an LLM extracts a
  few atomic facts that make the robot better at its mission. **Teams & supervisors persist
  forever; ordinary visitors expire** after a short TTL. On a new turn the relevant memories
  are recalled and injected as a compact "things you remember" block.

Layout under ``BRAIN_DIR`` (default ``<project>/brain/``, gitignored)::

    MEMORY.md              # the INDEX — one line per memory (read cheaply every turn/boot)
    memories/<id>.md       # atomic fact files: frontmatter + 1-3 sentence body
    sessions/<id>.json     # per-session snapshot (transcript + meta) — the host "copy"
    logs/YYYY-MM-DD.md     # human-readable daily session log (append-only)

Pure standard library (no vector DB, no embeddings) — retrieval is keyword/description
overlap blended with salience + recency decay, which is plenty for "remember a few teams
and supervisors". The Markdown files stay the rebuildable source of truth, so an embedding
channel can be blended into ``recall()`` later without changing the on-disk format.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from app.logging_setup import get_logger, log_exception
from app.state import ChatMessage
from config import settings

log = get_logger("app.memory")

NEVER = "never"
# Record types. Anything in _PERMANENT never expires; _PERSON expires on a short TTL.
_PERMANENT = {"supervisor", "team"}
_PERSON = {"person", "visitor"}
_VALID_TYPES = _PERMANENT | _PERSON | {"place", "task", "fact", "preference"}

_TOKEN = re.compile(r"\w+", re.UNICODE)
# Common words ignored when scoring relevance (EN + a few AR particles) — mirrors the
# knowledge-base retriever so memory and KB behave the same.
_STOP = {
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for",
    "what", "how", "do", "you", "i", "me", "my", "your", "can", "please", "tell",
    "في", "من", "الى", "على", "عن", "هل", "ما", "كيف", "هو", "هي", "و",
}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "") if t.lower() not in _STOP and len(t) > 1}


def _today() -> date:
    return datetime.now().date()


def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------
@dataclass
class MemoryRecord:
    id: str
    type: str
    subject: str
    content: str
    salience: float = 0.5
    tags: list[str] = field(default_factory=list)
    created: str = ""
    last_seen: str = ""
    expiry: str = NEVER  # ISO date or "never"
    name: str = ""

    @property
    def description(self) -> str:
        """One-line summary for the index (first sentence of the body, capped)."""
        first = re.split(r"(?<=[.!؟?])\s", self.content.strip(), maxsplit=1)[0]
        first = " ".join(first.split())
        return (first[:140]).replace("|", "/")

    def is_expired(self, today: Optional[date] = None) -> bool:
        if self.expiry == NEVER or self.type in _PERMANENT:
            return False
        d = _parse_date(self.expiry)
        return bool(d and d < (today or _today()))


# ---------------------------------------------------------------------------
# Brain
# ---------------------------------------------------------------------------
class Brain:
    """File-based long-term + session memory. Tolerant of a missing/empty brain dir."""

    enabled = True

    def __init__(self, brain_dir: Path, llm=None) -> None:
        self.dir = Path(brain_dir)
        self.memories_dir = self.dir / "memories"
        self.sessions_dir = self.dir / "sessions"
        self.logs_dir = self.dir / "logs"
        self.index_file = self.dir / "MEMORY.md"
        self.llm = llm  # LLMEngine, used only by remember_session()

    # ---- storage helpers --------------------------------------------------
    def _ensure_dirs(self) -> None:
        for d in (self.dir, self.memories_dir, self.sessions_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _record_path(self, rec_id: str) -> Path:
        return self.memories_dir / f"{rec_id}.md"

    def _serialise(self, rec: MemoryRecord) -> str:
        fm = [
            "---",
            f"id: {rec.id}",
            f"name: {rec.name or rec.subject}",
            f"type: {rec.type}",
            f"subject: {rec.subject}",
            f"salience: {rec.salience:.2f}",
            f"created: {rec.created}",
            f"last_seen: {rec.last_seen}",
            f"expiry: {rec.expiry}",
            f"tags: {', '.join(rec.tags)}",
            "---",
            "",
            rec.content.strip(),
            "",
        ]
        return "\n".join(fm)

    @staticmethod
    def _parse_file(text: str) -> Optional[MemoryRecord]:
        if not text.lstrip().startswith("---"):
            return None
        body_start = text.find("---", text.find("---") + 3)
        if body_start == -1:
            return None
        header = text[text.find("---") + 3:body_start]
        body = text[body_start + 3:].strip()
        meta: dict[str, str] = {}
        for line in header.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip()
        if not meta.get("id") or not meta.get("type"):
            return None
        try:
            salience = float(meta.get("salience", "0.5"))
        except ValueError:
            salience = 0.5
        tags = [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]
        return MemoryRecord(
            id=meta["id"], type=meta.get("type", "fact"), subject=meta.get("subject", ""),
            content=body, salience=salience, tags=tags, created=meta.get("created", ""),
            last_seen=meta.get("last_seen", ""), expiry=meta.get("expiry", NEVER),
            name=meta.get("name", ""),
        )

    def _load_all(self) -> list[MemoryRecord]:
        if not self.memories_dir.exists():
            return []
        out: list[MemoryRecord] = []
        for path in sorted(self.memories_dir.glob("*.md")):
            try:
                rec = self._parse_file(path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Could not read memory file %s", path)
                continue
            if rec:
                out.append(rec)
        return out

    def _rebuild_index(self, records: Optional[list[MemoryRecord]] = None) -> None:
        """Regenerate MEMORY.md from the memory files (the index is derived/rebuildable)."""
        records = records if records is not None else self._load_all()
        lines = [
            "# G1 robot brain — memory index (DERIVED from memories/*.md; safe to delete & rebuild).",
            "# fmt: id | type | subject | salience | last_seen | expiry | tags | description",
        ]
        for r in sorted(records, key=lambda x: (-x.salience, x.subject)):
            lines.append(" | ".join([
                r.id, r.type, r.subject.replace("|", "/"), f"{r.salience:.2f}",
                r.last_seen or "", r.expiry or NEVER, ",".join(r.tags).replace("|", "/"),
                r.description,
            ]))
        self._ensure_dirs()
        self._atomic_write(self.index_file, "\n".join(lines) + "\n")

    def _read_index(self) -> list[dict]:
        """Cheap scan of MEMORY.md for recall. Rebuilds it from files if missing."""
        if not self.index_file.exists():
            if self.memories_dir.exists() and any(self.memories_dir.glob("*.md")):
                self._rebuild_index()
            else:
                return []
        rows: list[dict] = []
        try:
            for line in self.index_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 8:
                    continue
                rows.append({
                    "id": parts[0], "type": parts[1], "subject": parts[2],
                    "salience": float(parts[3]) if parts[3] else 0.5,
                    "last_seen": parts[4], "expiry": parts[5],
                    "tags": [t for t in parts[6].split(",") if t], "description": parts[7],
                })
        except Exception:
            log_exception(log, "Reading memory index failed")
        return rows

    # ---- READ path --------------------------------------------------------
    def recall(self, query_text: str, k: Optional[int] = None) -> str:
        """Return a compact 'things you remember' block for the most relevant memories,
        or "" if nothing relevant. Reads the cheap index, scores, then loads only the
        top-k bodies — never the whole store."""
        k = k or settings.MEMORY_RECALL_K
        rows = self._read_index()
        if not rows:
            return ""
        q = _tokens(query_text)
        today = _today()
        scored: list[tuple[float, dict]] = []
        for row in rows:
            d = _parse_date(row["expiry"])
            if row["expiry"] != NEVER and d and d < today and row["type"] not in _PERMANENT:
                continue  # expired
            desc_tokens = _tokens(row["subject"] + " " + " ".join(row["tags"]) + " " + row["description"])
            overlap = len(q & desc_tokens) / (len(q) or 1)
            recency = self._recency(row["last_seen"], today)
            # Permanent facts (teams/supervisors) keep a small floor so the robot stays
            # aware of them even when the current turn doesn't mention them by keyword.
            base = overlap * 0.6 + row["salience"] * 0.25 + recency * 0.15
            if row["type"] in _PERMANENT:
                base = max(base, 0.18 + row["salience"] * 0.1)
            scored.append((base, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = [row for score, row in scored if score > 0.16][:k]
        if not chosen:
            return ""
        bodies: list[str] = []
        for row in chosen:
            body = self._load_body(row["id"]) or row["description"]
            subj = row["subject"]
            bodies.append(f"- {subj}: {body}" if subj and subj.lower() not in body.lower() else f"- {body}")
        return ("Things you remember (use only if relevant; don't recite verbatim):\n"
                + "\n".join(bodies))

    @staticmethod
    def _recency(last_seen: str, today: date) -> float:
        d = _parse_date(last_seen)
        if not d:
            return 0.3
        days = max(0, (today - d).days)
        return 0.5 ** (days / 30.0)  # 30-day half-life

    def _load_body(self, rec_id: str) -> str:
        path = self._record_path(rec_id)
        if not path.exists():
            return ""
        rec = self._parse_file(path.read_text(encoding="utf-8"))
        return rec.content if rec else ""

    # ---- session snapshot (always, when MEMORY_SESSION_SNAPSHOTS) ----------
    def snapshot_session(self, history: list[ChatMessage], session_id: Optional[str] = None) -> Optional[Path]:
        """Write a JSON snapshot of the finished session + append a daily-log entry.
        Returns the snapshot path, or None if there was nothing to save."""
        turns = [{"role": m.role, "content": m.content} for m in history if m.role in ("user", "assistant")]
        if not turns:
            return None
        now = datetime.now()
        sid = session_id or now.strftime("%Y%m%d-%H%M%S")
        try:
            self._ensure_dirs()
            snap = {"id": sid, "ended_at": now.isoformat(timespec="seconds"),
                    "turn_count": len(turns), "transcript": turns}
            self._atomic_write(self.sessions_dir / f"{sid}.json",
                               json.dumps(snap, ensure_ascii=False, indent=2))
            self._append_log(now, sid, turns)
            log.info("Session snapshot saved: %s (%d turns)", sid, len(turns))
            return self.sessions_dir / f"{sid}.json"
        except Exception:
            log_exception(log, "Saving session snapshot failed")
            return None

    def _append_log(self, now: datetime, sid: str, turns: list[dict]) -> None:
        log_path = self.logs_dir / f"{now.strftime('%Y-%m-%d')}.md"
        lines = [f"\n## Session {sid} — {now.strftime('%H:%M')}  ({len(turns)} turns)"]
        for t in turns:
            who = "Visitor" if t["role"] == "user" else "Robot"
            lines.append(f"- **{who}:** {' '.join(t['content'].split())}")
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    # ---- WRITE path (long-term only) --------------------------------------
    async def remember_session(self, history: list[ChatMessage]) -> int:
        """Ask the LLM to extract atomic, mission-useful memories from the session and
        persist them (importance gate + type-based TTL + ADD/UPDATE). Returns how many
        records were written/updated. No-op without an LLM or conversation."""
        if self.llm is None:
            return 0
        turns = [m for m in history if m.role in ("user", "assistant")]
        if not turns:
            return 0
        transcript = "\n".join(
            f"{'Visitor' if m.role == 'user' else 'Robot'}: {m.content}" for m in turns
        )
        try:
            raw = await self.llm.complete([ChatMessage(role="system", content=_EXTRACT_PROMPT),
                                           ChatMessage(role="user", content=transcript)])
        except Exception:
            log_exception(log, "Memory extraction LLM call failed")
            return 0
        candidates = _parse_candidates(raw)
        if not candidates:
            return 0
        existing = self._load_all()
        today = _today()
        saved = 0
        for cand in candidates:
            rec = self._normalise(cand, today)
            if rec is None:
                continue
            match = self._find(existing, rec.type, rec.subject)
            if match:
                self._merge(match, rec, today)
                self._atomic_write(self._record_path(match.id), self._serialise(match))
            else:
                existing.append(rec)
                self._atomic_write(self._record_path(rec.id), self._serialise(rec))
            saved += 1
        if saved:
            self._rebuild_index(existing)
            log.info("Long-term memory: %d record(s) written/updated.", saved)
        return saved

    def _normalise(self, cand: dict, today: date) -> Optional[MemoryRecord]:
        rtype = str(cand.get("type", "fact")).strip().lower()
        if rtype not in _VALID_TYPES:
            rtype = "fact"
        subject = str(cand.get("subject", "")).strip()
        content = str(cand.get("content", "")).strip()
        if not content:
            return None
        try:
            salience = max(0.0, min(1.0, float(cand.get("salience", 0.5))))
        except (TypeError, ValueError):
            salience = 0.5
        # Importance gate — drop low-value records unless they're a team/supervisor.
        if rtype not in _PERMANENT and salience < settings.MEMORY_SALIENCE_FLOOR:
            return None
        if rtype in _PERMANENT:
            salience = max(salience, 0.8)
        tags = cand.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        tags = [str(t).strip() for t in tags if str(t).strip()][:6]
        iso = today.isoformat()
        return MemoryRecord(
            id="m_" + uuid.uuid4().hex[:8], type=rtype, subject=subject, content=content,
            salience=salience, tags=tags, created=iso, last_seen=iso,
            expiry=self._ttl_for(rtype, today), name=_slug(subject or content),
        )

    @staticmethod
    def _ttl_for(rtype: str, today: date) -> str:
        if rtype in _PERMANENT:
            return NEVER
        days = settings.MEMORY_VISITOR_TTL_DAYS if rtype in _PERSON else settings.MEMORY_FACT_TTL_DAYS
        if days <= 0:
            return NEVER
        from datetime import timedelta
        return (today + timedelta(days=days)).isoformat()

    @staticmethod
    def _find(records: list[MemoryRecord], rtype: str, subject: str) -> Optional[MemoryRecord]:
        s = subject.strip().lower()
        if not s:
            return None
        for r in records:
            if r.type == rtype and r.subject.strip().lower() == s:
                return r
        return None

    def _merge(self, target: MemoryRecord, fresh: MemoryRecord, today: date) -> None:
        target.last_seen = today.isoformat()
        target.salience = max(target.salience, fresh.salience)
        if fresh.content and fresh.content.lower() not in target.content.lower():
            target.content = (target.content.rstrip() + " " + fresh.content).strip()
        target.tags = list(dict.fromkeys(target.tags + fresh.tags))[:6]
        if target.type not in _PERMANENT:  # re-mention extends the visitor's life
            target.expiry = self._ttl_for(target.type, today)

    # ---- forgetting -------------------------------------------------------
    def sweep_expired(self) -> int:
        """Delete expired memory files (never teams/supervisors). Called once at boot."""
        records = self._load_all()
        today = _today()
        keep, removed = [], 0
        for r in records:
            if r.is_expired(today):
                try:
                    self._record_path(r.id).unlink(missing_ok=True)
                    removed += 1
                except Exception:
                    log.warning("Could not remove expired memory %s", r.id)
                    keep.append(r)
            else:
                keep.append(r)
        if removed:
            self._rebuild_index(keep)
            log.info("Brain: swept %d expired memory record(s).", removed)
        return removed

    def forget_visitors(self) -> int:
        """Drop every ordinary-visitor record now (panel 'Forget visitors' button)."""
        records = self._load_all()
        keep, removed = [], 0
        for r in records:
            if r.type in _PERSON:
                self._record_path(r.id).unlink(missing_ok=True)
                removed += 1
            else:
                keep.append(r)
        if removed:
            self._rebuild_index(keep)
        return removed

    def stats(self) -> dict:
        records = self._load_all()
        today = _today()
        by_type: dict[str, int] = {}
        expiring = 0
        for r in records:
            by_type[r.type] = by_type.get(r.type, 0) + 1
            d = _parse_date(r.expiry)
            if r.expiry != NEVER and d and (d - today).days <= 3 and r.type not in _PERMANENT:
                expiring += 1
        sessions = len(list(self.sessions_dir.glob("*.json"))) if self.sessions_dir.exists() else 0
        return {"total": len(records), "by_type": by_type, "expiring_soon": expiring, "sessions": sessions}


# ---------------------------------------------------------------------------
# Null brain (no-op) — used when the brain dir can't be created, and in tests.
# ---------------------------------------------------------------------------
class NullBrain:
    enabled = False

    def recall(self, query_text: str, k=None) -> str:
        return ""

    def snapshot_session(self, history, session_id=None):
        return None

    async def remember_session(self, history) -> int:
        return 0

    def sweep_expired(self) -> int:
        return 0

    def forget_visitors(self) -> int:
        return 0

    def stats(self) -> dict:
        return {"total": 0, "by_type": {}, "expiring_soon": 0, "sessions": 0}


def _slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", (text or "").strip().lower(), flags=re.UNICODE).strip("-")
    return s[:40] or "memory"


_EXTRACT_PROMPT = """\
You are the memory of a humanoid robot built by Altkamul that greets and talks with people. \
From the conversation transcript, extract ONLY durable facts that would make the robot better \
at its mission in FUTURE conversations. Most small-talk turns yield NOTHING — return an empty \
list rather than padding.

Rules:
- Persist TEAMS and SUPERVISORS the robot works with (type "team" / "supervisor").
- Record an ordinary visitor only as type "person" with LOW salience — their name is short-lived.
- Other useful durable facts: type "fact", "preference", "place", or "task".
- Do NOT store secrets, one-off chit-chat, the robot's own lines, or anything already obvious.

Return a JSON array (and nothing else). Each item:
  {"type": "...", "subject": "<who/what it's about>", "content": "<one short factual sentence>", \
"salience": 0.0-1.0, "tags": ["..."]}
If there is nothing worth remembering, return [].
"""


def _parse_candidates(raw: str) -> list[dict]:
    """Best-effort JSON-array extraction from an LLM reply (tolerant of code fences/prose)."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Prefer the outermost [...] array.
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("memories") or data.get("records") or []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]
