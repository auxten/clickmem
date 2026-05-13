"""Claude Code adapter — reads ``~/.claude/projects/*/{*.jsonl, memory/*.md}``."""

from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import (
    RawSession,
    base_url_default,
    home,
    iter_jsonl,
    safe_mtime,
    safe_remove,
)

_log = logging.getLogger(__name__)

name = "claude_code"
label = "Claude Code"
experimental = False

_BASE = home() / ".claude" / "projects"
_SETTINGS = home() / ".claude" / "settings.json"
_SKIP_TYPES = frozenset({"queue-operation", "file-history-snapshot", "progress", "last-prompt", "summary"})


def detect() -> bool:
    return _BASE.is_dir() or (home() / ".claude").is_dir()


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", "") or "")
            elif block.get("type") == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str):
                    parts.append(inner)
        return "\n".join(p for p in parts if p).strip()
    return ""


def _parse_session(path: Path) -> RawSession | None:
    messages: list[str] = []
    cwd = ""
    for obj in iter_jsonl(path):
        if obj.get("type", "") in _SKIP_TYPES:
            continue
        if not cwd:
            cwd = obj.get("cwd", "") or ""
        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        text = _extract_text(msg.get("content", ""))
        if not text:
            continue
        if role in ("user", "assistant"):
            messages.append(f"{role}: {text}")
    if not messages:
        return None
    return RawSession(
        session_id=path.stem,
        agent=name,
        text="\n".join(messages),
        cwd=cwd,
        meta={"path": str(path)},
    )


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    if not _BASE.is_dir():
        return
    files = [Path(p) for p in glob.glob(str(_BASE / "*" / "*.jsonl"))]
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        info = _parse_session(path)
        if info and len(info.text) >= 50:
            yield info


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    if _BASE.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(_BASE / "*" / "memory" / "*.md")))
    return paths


def _load_settings() -> dict[str, Any]:
    if not _SETTINGS.is_file():
        return {}
    try:
        return json.loads(_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    _SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install_hooks(server_url: str = "") -> dict[str, Any]:
    """Write ``~/.claude/settings.json`` with only ``SessionStart`` + ``Stop`` HTTP hooks."""
    base = (server_url or os.environ.get("CLICKMEM_REMOTE") or base_url_default()).rstrip("/")
    data = _load_settings()
    hooks = data.setdefault("hooks", {})

    recall_url = f"{base}/v1/recall"
    raw_url = f"{base}/v1/raw"

    hooks["SessionStart"] = [{
        "hooks": [{"type": "http", "url": recall_url, "timeout": 15}],
    }]
    hooks["Stop"] = [{
        "hooks": [{"type": "http", "url": raw_url, "timeout": 15, "async": True}],
    }]
    for legacy in ("UserPromptSubmit", "PostToolUse", "SessionEnd"):
        hooks.pop(legacy, None)

    _save_settings(data)
    return {"ok": True, "installed": True, "agent": name, "settings": str(_SETTINGS), "recall_url": recall_url, "raw_url": raw_url}


def uninstall_hooks() -> dict[str, Any]:
    data = _load_settings()
    hooks = data.get("hooks", {}) or {}
    removed = []
    for key in ("SessionStart", "Stop", "UserPromptSubmit", "PostToolUse", "SessionEnd"):
        if key in hooks:
            hooks.pop(key, None)
            removed.append(key)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _save_settings(data)
    return {"ok": True, "installed": False, "agent": name, "removed": removed}


def export_blob(dst_path: Path) -> dict[str, Any]:
    """Copy a thin manifest of session paths into ``dst_path`` (one JSON file)."""
    items = []
    for p in (Path(x) for x in glob.glob(str(_BASE / "*" / "*.jsonl"))):
        items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}
