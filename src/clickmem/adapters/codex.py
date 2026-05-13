"""Codex CLI adapter — reads ``~/.codex/sessions/**/rollout-*.jsonl``."""

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
)

_log = logging.getLogger(__name__)

name = "codex"
label = "Codex CLI"
experimental = False

_BASE = home() / ".codex"
_HOOKS_JSON = _BASE / "hooks.json"


def detect() -> bool:
    return _BASE.is_dir()


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text", "") or ""
            if not text:
                continue
            if text.startswith("<permissions ") or text.startswith("<app-context>"):
                continue
            if text.startswith("<collaboration_mode>") or text.startswith("<skills_instructions>"):
                continue
            if text.startswith("<environment_context>"):
                continue
            parts.append(text)
        return "\n".join(p for p in parts if p).strip()
    return ""


def _parse_session(path: Path) -> RawSession | None:
    messages: list[str] = []
    cwd = ""
    for obj in iter_jsonl(path):
        rec_type = obj.get("type", "")
        payload = obj.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if rec_type in ("session_meta", "turn_context") and not cwd:
            cwd = payload.get("cwd", "") or ""
            continue
        if rec_type == "response_item" and payload.get("type") == "message":
            role = payload.get("role", "")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(payload.get("content", []))
            if text:
                messages.append(f"{role}: {text}")
        elif rec_type == "event_msg" and payload.get("type") == "user_message":
            msg = payload.get("message", "")
            if msg and len(msg) > 5:
                messages.append(f"user: {msg}")
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
    sessions_dir = _BASE / "sessions"
    if not sessions_dir.is_dir():
        return
    files: list[Path] = []
    for root, _dirs, names in os.walk(sessions_dir):
        for fname in names:
            if fname.endswith(".jsonl") and fname.startswith("rollout-"):
                files.append(Path(root) / fname)
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        info = _parse_session(path)
        if info and len(info.text) >= 50:
            yield info


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    if (_BASE / "AGENTS.md").is_file():
        paths.append(_BASE / "AGENTS.md")
    memories = _BASE / "memories"
    if memories.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(memories / "*.md")))
    return paths


def _load_hooks() -> dict[str, Any]:
    if not _HOOKS_JSON.is_file():
        return {}
    try:
        return json.loads(_HOOKS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_hooks(data: dict[str, Any]) -> None:
    _HOOKS_JSON.parent.mkdir(parents=True, exist_ok=True)
    _HOOKS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install_hooks(server_url: str = "") -> dict[str, Any]:
    """Write ``~/.codex/hooks.json`` with one ``on_session_end`` HTTP hook."""
    base = (server_url or os.environ.get("CLICKMEM_REMOTE") or base_url_default()).rstrip("/")
    data = _load_hooks()
    hooks = data.setdefault("hooks", {})

    raw_url = f"{base}/v1/raw"
    recall_url = f"{base}/v1/recall"

    hooks["on_session_start"] = [{"type": "http", "url": recall_url, "timeout": 15}]
    hooks["on_session_end"] = [{"type": "http", "url": raw_url, "timeout": 15, "async": True}]

    _save_hooks(data)
    return {"ok": True, "installed": True, "agent": name, "path": str(_HOOKS_JSON), "raw_url": raw_url}


def uninstall_hooks() -> dict[str, Any]:
    data = _load_hooks()
    removed = []
    hooks = data.get("hooks", {}) or {}
    for key in ("on_session_start", "on_session_end"):
        if key in hooks:
            hooks.pop(key, None)
            removed.append(key)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _save_hooks(data)
    return {"ok": True, "installed": False, "agent": name, "removed": removed}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    sessions_dir = _BASE / "sessions"
    if sessions_dir.is_dir():
        for root, _dirs, names in os.walk(sessions_dir):
            for fname in names:
                if fname.endswith(".jsonl") and fname.startswith("rollout-"):
                    p = Path(root) / fname
                    items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}
