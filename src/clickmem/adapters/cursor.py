"""Cursor adapter — reads ``~/.cursor/projects/*/agent-transcripts/*/*.jsonl``.

Hook install bundles the slim ``cursor-hooks/`` package (shipped in repo root)
into ``~/.cursor/plugins/clickmem/``.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import (
    RawSession,
    home,
    iter_jsonl,
    safe_mtime,
)

_log = logging.getLogger(__name__)

name = "cursor"
label = "Cursor"
experimental = False

_BASE = home() / ".cursor" / "projects"
_PLUGIN_DST = home() / ".cursor" / "plugins" / "clickmem"


def detect() -> bool:
    return _BASE.is_dir() or (home() / ".cursor").is_dir()


def _decode_slug(slug: str) -> str:
    if not slug:
        return ""
    parts = slug.split("-")
    if len(parts) >= 2 and parts[0] == "Users":
        path = f"/Users/{parts[1]}"
        for p in parts[2:]:
            trial_slash = path + "/" + p
            trial_dash = path + "-" + p
            if Path(trial_slash).is_dir():
                path = trial_slash
            elif Path(trial_dash).is_dir():
                path = trial_dash
            else:
                path = trial_slash
        return path
    return "/" + slug.replace("-", "/")


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p).strip()
    return ""


def _parse_session(path: Path) -> RawSession | None:
    messages: list[str] = []
    parts = str(path).split(os.sep)
    project_slug = ""
    for i, p in enumerate(parts):
        if p == "agent-transcripts" and i > 0:
            project_slug = parts[i - 1]
            break
    cwd = _decode_slug(project_slug)

    for obj in iter_jsonl(path):
        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = obj.get("role", "")
        text = _extract_text(msg.get("content", ""))
        if text and role in ("user", "assistant"):
            messages.append(f"{role}: {text}")
    if not messages:
        return None
    return RawSession(
        session_id=path.stem,
        agent=name,
        text="\n".join(messages),
        cwd=cwd,
        meta={"path": str(path), "mtime": datetime.fromtimestamp(safe_mtime(path), tz=timezone.utc).isoformat()},
    )


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    if not _BASE.is_dir():
        return
    files = [Path(p) for p in glob.glob(str(_BASE / "*" / "agent-transcripts" / "*" / "*.jsonl"))
             if "/subagents/" not in p]
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        info = _parse_session(path)
        if info and len(info.text) >= 50:
            yield info


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    global_rules = home() / ".cursor" / "rules"
    if global_rules.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(global_rules / "*.md")))
        paths.extend(Path(p) for p in glob.glob(str(global_rules / "*.mdc")))
    return paths


def _repo_cursor_hooks_dir() -> Path | None:
    """Find the repo's ``cursor-hooks/`` source tree (used by ``install_hooks``)."""
    here = Path(__file__).resolve()
    # walk upwards from clickmem/adapters/cursor.py looking for repo root
    for parent in here.parents:
        candidate = parent / "cursor-hooks"
        if (candidate / "hooks" / "hooks.json").is_file():
            return candidate
    return None


def install_hooks(server_url: str = "") -> dict[str, Any]:
    """Copy ``cursor-hooks/`` source tree to ``~/.cursor/plugins/clickmem/``.

    The stop-hook resolves the server URL at runtime from
    ``CLICKMEM_REMOTE`` / ``CLICKMEM_SERVER_HOST`` / ``CLICKMEM_SERVER_PORT``,
    so the install path itself does not need to bake the URL in.
    """
    src = _repo_cursor_hooks_dir()
    if src is None:
        return {
            "ok": False,
            "installed": False,
            "agent": name,
            "error": "cursor-hooks/ source tree not bundled with this clickmem install",
            "hint": "this needs a real install path when shipped as a pip wheel; see Phase 10 packaging",
        }

    _PLUGIN_DST.parent.mkdir(parents=True, exist_ok=True)
    if _PLUGIN_DST.exists():
        shutil.rmtree(_PLUGIN_DST)
    shutil.copytree(src, _PLUGIN_DST)

    if server_url:
        env_file = _PLUGIN_DST / "hooks" / ".env"
        env_file.write_text(f"CLICKMEM_REMOTE={server_url.rstrip('/')}\n", encoding="utf-8")

    return {"ok": True, "installed": True, "agent": name, "path": str(_PLUGIN_DST), "source": str(src)}


def uninstall_hooks() -> dict[str, Any]:
    if _PLUGIN_DST.exists():
        try:
            shutil.rmtree(_PLUGIN_DST)
            return {"ok": True, "installed": False, "agent": name, "path": str(_PLUGIN_DST)}
        except OSError as e:
            return {"ok": False, "installed": True, "agent": name, "error": str(e)}
    return {"ok": True, "installed": False, "agent": name, "message": "no plugin directory"}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items = []
    if _BASE.is_dir():
        for p in (Path(x) for x in glob.glob(str(_BASE / "*" / "agent-transcripts" / "*" / "*.jsonl"))):
            if "/subagents/" in str(p):
                continue
            items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}
