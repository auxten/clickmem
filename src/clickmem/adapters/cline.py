"""Cline adapter — experimental, doc-only.

Cline stores sessions in VSCode globalStorage under
``~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/`` (or the
macOS equivalent). The format is upgraded between versions; we keep the
parser intentionally tolerant and log a clear ``experimental`` warning.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import platform
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import (
    RawSession,
    home,
    safe_mtime,
)

_log = logging.getLogger(__name__)

name = "cline"
label = "Cline"
experimental = True


def _candidate_bases() -> list[Path]:
    sys = platform.system().lower()
    if sys == "darwin":
        root = home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage"
    elif sys.startswith("linux"):
        root = home() / ".config" / "Code" / "User" / "globalStorage"
    else:
        root = home() / "AppData" / "Roaming" / "Code" / "User" / "globalStorage"
    return [root / "saoudrizwan.claude-dev"]


def detect() -> bool:
    found = any(p.is_dir() for p in _candidate_bases())
    if found:
        _log.info("clickmem.adapters.cline: experimental adapter; format may change between Cline versions")
    return found


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    for base in _candidate_bases():
        tasks = base / "tasks"
        if not tasks.is_dir():
            continue
        for task_dir in tasks.iterdir():
            if not task_dir.is_dir():
                continue
            mtime = safe_mtime(task_dir)
            if since is not None and mtime < since:
                continue
            history = task_dir / "api_conversation_history.json"
            if not history.is_file():
                continue
            try:
                data = json.loads(history.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, list):
                continue
            messages: list[str] = []
            for m in data:
                if not isinstance(m, dict):
                    continue
                role = m.get("role", "")
                content = m.get("content", "")
                if isinstance(content, list):
                    content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
                text = (content or "").strip() if isinstance(content, str) else ""
                if role in ("user", "assistant") and text:
                    messages.append(f"{role}: {text}")
            if not messages:
                continue
            yield RawSession(
                session_id=task_dir.name,
                agent=name,
                text="\n".join(messages),
                meta={"path": str(task_dir)},
            )


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    for base in _candidate_bases():
        settings = base / "settings.json"
        if settings.is_file():
            paths.append(settings)
    return paths


def install_hooks(server_url: str = "") -> dict[str, Any]:
    raise NotImplementedError(
        "cline adapter is experimental and doc-only: Cline has no documented hook surface."
    )


def uninstall_hooks() -> dict[str, Any]:
    return {"ok": True, "installed": False, "agent": name, "message": "doc-only adapter"}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for base in _candidate_bases():
        tasks = base / "tasks"
        if tasks.is_dir():
            for d in tasks.iterdir():
                if d.is_dir():
                    items.append({"path": str(d), "mtime": safe_mtime(d)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items, "experimental": True}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path), "experimental": True}
