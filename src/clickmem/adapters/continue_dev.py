"""Continue.dev adapter — reads ``~/.continue/sessions/*.json`` and config."""

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
    safe_mtime,
)

_log = logging.getLogger(__name__)

name = "continue_dev"
label = "Continue.dev"
experimental = False

_BASE = home() / ".continue"
_CONFIG = _BASE / "config.json"


def detect() -> bool:
    return _BASE.is_dir()


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    sessions_dir = _BASE / "sessions"
    if not sessions_dir.is_dir():
        return
    files = [Path(p) for p in glob.glob(str(sessions_dir / "*.json"))]
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        history = data.get("history") or data.get("messages") or []
        if not isinstance(history, list):
            continue
        messages: list[str] = []
        for h in history:
            if not isinstance(h, dict):
                continue
            role = h.get("role", "")
            content = h.get("content", "")
            if isinstance(content, list):
                content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
            text = (content or "").strip()
            if role in ("user", "assistant") and text:
                messages.append(f"{role}: {text}")
        if not messages:
            continue
        sid = data.get("sessionId") or path.stem
        yield RawSession(
            session_id=str(sid),
            agent=name,
            text="\n".join(messages),
            meta={"path": str(path), "title": data.get("title", "")},
        )


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    if _CONFIG.is_file():
        paths.append(_CONFIG)
    rules = _BASE / "rules"
    if rules.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(rules / "*.md")))
    return paths


def _load_config() -> dict[str, Any]:
    if not _CONFIG.is_file():
        return {}
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(data: dict[str, Any]) -> None:
    _CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install_hooks(server_url: str = "") -> dict[str, Any]:
    """Register a ``contextProviders`` http entry pointing at ``/v1/recall``.

    Continue does not yet expose a stop-hook surface; we wire recall only.
    The raw landing path will activate when Continue adds a stop event.
    """
    base = (server_url or os.environ.get("CLICKMEM_REMOTE") or base_url_default()).rstrip("/")
    data = _load_config()
    providers = data.setdefault("contextProviders", [])
    if not isinstance(providers, list):
        providers = []
        data["contextProviders"] = providers
    providers[:] = [p for p in providers if not (isinstance(p, dict) and p.get("name") == "clickmem")]
    providers.append({
        "name": "clickmem",
        "params": {"url": f"{base}/v1/recall"},
    })
    _save_config(data)
    return {
        "ok": True,
        "installed": True,
        "agent": name,
        "path": str(_CONFIG),
        "note": "Continue currently exposes only context-provider hooks; raw landing requires the upcoming stop-event surface.",
    }


def uninstall_hooks() -> dict[str, Any]:
    data = _load_config()
    providers = data.get("contextProviders", [])
    if not isinstance(providers, list):
        return {"ok": True, "installed": False, "agent": name}
    before = len(providers)
    providers[:] = [p for p in providers if not (isinstance(p, dict) and p.get("name") == "clickmem")]
    data["contextProviders"] = providers
    _save_config(data)
    return {"ok": True, "installed": False, "agent": name, "removed": before - len(providers)}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items = []
    sessions_dir = _BASE / "sessions"
    if sessions_dir.is_dir():
        for p in (Path(x) for x in glob.glob(str(sessions_dir / "*.json"))):
            items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}
