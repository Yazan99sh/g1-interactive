"""Read/write the app's ``.env`` while preserving comments, section headers and key
order — so editing one key from the panel never scrambles the file.

A file is parsed into an ordered list of items (blank / comment / section / kv).
``grouped()`` turns it into the API shape (sections of fields, secrets masked);
``update()`` rewrites only the values of provided keys and re-serializes losslessly.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SECTION_RE = re.compile(r"^#\s*-+\s*(.*?)\s*-+\s*$")
_SECRET_RE = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)


def _mask(value: str) -> str:
    if not value:
        return "MISSING"
    if len(value) > 12:
        return f"{value[:6]}…{value[-4:]}"
    return "set"


def is_secret(key: str) -> bool:
    return bool(_SECRET_RE.search(key))


def load(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not path.exists():
        return items
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped:
            items.append({"kind": "blank", "raw": raw})
        elif stripped.startswith("#"):
            m = _SECTION_RE.match(stripped)
            if m and m.group(1):
                items.append({"kind": "section", "raw": raw, "title": m.group(1)})
            else:
                items.append({"kind": "comment", "raw": raw})
        elif "=" in raw:
            key, value = raw.split("=", 1)
            items.append({"kind": "kv", "raw": raw, "key": key.strip(), "value": value.strip()})
        else:
            items.append({"kind": "comment", "raw": raw})
    return items


def grouped(path: Path) -> list[dict[str, Any]]:
    """Return [{title, fields:[{key,value,secret,masked_value,comment}]}]. Secret
    field values are omitted (only masked_value is sent to the browser)."""
    groups: list[dict[str, Any]] = []
    current = {"title": "General", "fields": []}
    pending: list[str] = []
    for item in load(path):
        if item["kind"] == "section":
            if current["fields"]:
                groups.append(current)
            current = {"title": item["title"], "fields": []}
            pending = []
        elif item["kind"] == "comment":
            pending.append(item["raw"].lstrip("# ").rstrip())
        elif item["kind"] == "blank":
            pending = []
        elif item["kind"] == "kv":
            secret = is_secret(item["key"])
            current["fields"].append({
                "key": item["key"],
                "value": "" if secret else item["value"],
                "secret": secret,
                "masked_value": _mask(item["value"]) if secret else item["value"],
                "comment": " ".join(pending).strip(),
            })
            pending = []
    if current["fields"]:
        groups.append(current)
    return groups


def get(path: Path, key: str) -> str | None:
    for item in load(path):
        if item["kind"] == "kv" and item["key"] == key:
            return item["value"]
    return None


def update(path: Path, updates: dict[str, str]) -> None:
    """Rewrite ``path`` setting the given keys. Existing keys are updated in place;
    unknown keys are appended under a panel-managed section. All comments preserved."""
    items = load(path)
    remaining = dict(updates)
    out_lines: list[str] = []
    for item in items:
        if item["kind"] == "kv" and item["key"] in remaining:
            value = remaining.pop(item["key"])
            out_lines.append(f"{item['key']}={value}")
        else:
            out_lines.append(item["raw"])
    if remaining:
        out_lines.append("")
        out_lines.append("# ---- Added by control panel ----")
        for key, value in remaining.items():
            out_lines.append(f"{key}={value}")
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
