"""Hermes Agent adapter — gateway hook + memory/doc import.

Hermes stores user memory and agent configuration under ``~/.hermes`` and
loads gateway hooks from ``~/.hermes/hooks/<name>/``. This adapter writes a
small gateway hook that lands completed agent/session events in ClickMem raw
storage while keeping recall explicit through REST/MCP.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import RawSession, base_url_default, home, read_text, safe_mtime

name = "hermes"
label = "Hermes Agent"
experimental = False


def _hermes_home() -> Path:
    override = os.environ.get("HERMES_HOME")
    if override:
        return Path(os.path.expanduser(override))
    return home() / ".hermes"


def _hook_dir() -> Path:
    return _hermes_home() / "hooks" / "clickmem"


def detect() -> bool:
    return _hermes_home().is_dir()


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    logs_dir = _hermes_home() / "logs"
    if not logs_dir.is_dir():
        return
    files = [Path(p) for p in glob.glob(str(logs_dir / "*.jsonl"))]
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        text = read_text(path)
        if len(text.strip()) < 50:
            continue
        yield RawSession(
            session_id=path.stem,
            agent=name,
            text=text,
            meta={"path": str(path), "source": "hermes-log"},
        )


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    root = _hermes_home()
    for fname in (
        "AGENTS.md",
        "MEMORY.md",
        "USER.md",
        "SOUL.md",
        "TOOLS.md",
        "IDENTITY.md",
        "BOOT.md",
        "config.yaml",
    ):
        p = root / fname
        if p.is_file():
            paths.append(p)
    memories = root / "memories"
    if memories.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(memories / "*.md")))
    return paths


def _write_hook(raw_url: str) -> None:
    hook_dir = _hook_dir()
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "HOOK.yaml").write_text(
        """name: clickmem
description: Land Hermes lifecycle events in ClickMem raw storage
events:
  - agent:end
  - session:end
  - session:reset
""",
        encoding="utf-8",
    )
    (hook_dir / "handler.py").write_text(
        f'''"""ClickMem raw landing hook for Hermes Agent."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

RAW_URL = "{raw_url}"


def _text(context: dict) -> str:
    for key in ("response", "message", "transcript", "summary"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return json.dumps(context, ensure_ascii=False, indent=2, default=str)


def _post(event_type: str, context: dict) -> None:
    base = os.environ.get("CLICKMEM_REMOTE", "").rstrip("/")
    url = f"{{base}}/v1/raw" if base else RAW_URL
    session_id = (
        context.get("session_id")
        or context.get("session_key")
        or context.get("sessionKey")
        or f"hermes-{{os.getpid()}}"
    )
    body = json.dumps({{
        "text": _text(context),
        "session_id": str(session_id),
        "agent": "hermes",
        "role": event_type,
        "meta": {{"event_type": event_type}},
    }}).encode("utf-8")
    headers = {{"content-type": "application/json"}}
    api_key = os.environ.get("CLICKMEM_API_KEY")
    if api_key:
        headers["authorization"] = f"Bearer {{api_key}}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except (OSError, urllib.error.URLError):
        pass


def handle(event_type: str, context: dict):
    threading.Thread(target=_post, args=(event_type, context or {{}}), daemon=True).start()
''',
        encoding="utf-8",
    )


def install_hooks(server_url: str = "") -> dict[str, Any]:
    base = (server_url or os.environ.get("CLICKMEM_REMOTE") or base_url_default()).rstrip("/")
    raw_url = f"{base}/v1/raw"
    _write_hook(raw_url)
    return {
        "ok": True,
        "installed": True,
        "agent": name,
        "path": str(_hook_dir()),
        "raw_url": raw_url,
        "note": "restart the Hermes gateway so gateway hooks reload",
    }


def uninstall_hooks() -> dict[str, Any]:
    removed: list[str] = []
    hook_dir = _hook_dir()
    if hook_dir.exists():
        shutil.rmtree(hook_dir)
        removed.append(str(hook_dir))
    return {"ok": True, "installed": False, "agent": name, "removed": removed}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items = [{"path": str(p), "mtime": safe_mtime(p)} for p in iter_doc_paths()]
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}
