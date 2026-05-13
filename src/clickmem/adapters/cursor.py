"""Cursor adapter — reads ``~/.cursor/projects/*/agent-transcripts/*/*.jsonl``.

Hook install bundles the slim ``cursor-hooks/`` package (shipped in repo root)
into ``~/.cursor/hooks/clickmem/`` (matches Cursor's modern hooks layout and the
project rule "hooks source code in project tree, not .cursor/"). The legacy
``~/.cursor/plugins/clickmem/`` install path is still recognised by
:func:`detect` and cleaned up by :func:`uninstall_hooks` so the rename is
transparent for existing users.
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
_HOOK_DST = home() / ".cursor" / "hooks" / "clickmem"
_LEGACY_PLUGIN_DST = home() / ".cursor" / "plugins" / "clickmem"


def detect() -> bool:
    if _BASE.is_dir() or (home() / ".cursor").is_dir():
        return True
    # Defensive: even if ~/.cursor was scrubbed, recognise a lingering install.
    return _HOOK_DST.exists() or _LEGACY_PLUGIN_DST.exists()


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
    """Copy ``cursor-hooks/`` source tree to ``~/.cursor/hooks/clickmem/``.

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

    _HOOK_DST.parent.mkdir(parents=True, exist_ok=True)
    if _HOOK_DST.exists() or _HOOK_DST.is_symlink():
        if _HOOK_DST.is_symlink() or _HOOK_DST.is_file():
            _HOOK_DST.unlink()
        else:
            shutil.rmtree(_HOOK_DST)
    shutil.copytree(src, _HOOK_DST)

    if server_url:
        env_file = _HOOK_DST / "hooks" / ".env"
        env_file.write_text(f"CLICKMEM_REMOTE={server_url.rstrip('/')}\n", encoding="utf-8")

    return {"ok": True, "installed": True, "agent": name, "path": str(_HOOK_DST), "source": str(src)}


def _remove_path(p: Path) -> tuple[bool, str | None]:
    """Best-effort remove a directory, file, or symlink. Returns (existed, error)."""
    if not (p.exists() or p.is_symlink()):
        return False, None
    try:
        if p.is_symlink() or p.is_file():
            p.unlink()
        else:
            shutil.rmtree(p)
        return True, None
    except OSError as e:
        return True, str(e)


def uninstall_hooks() -> dict[str, Any]:
    """Remove the modern install path, plus best-effort the legacy plugin path."""
    removed: list[str] = []
    errors: list[str] = []

    for path in (_HOOK_DST, _LEGACY_PLUGIN_DST):
        existed, err = _remove_path(path)
        if err:
            errors.append(f"{path}: {err}")
        elif existed:
            removed.append(str(path))

    if errors:
        return {
            "ok": False,
            "installed": True,
            "agent": name,
            "removed": removed,
            "error": "; ".join(errors),
        }
    if not removed:
        return {"ok": True, "installed": False, "agent": name, "message": "no hook directory"}
    return {"ok": True, "installed": False, "agent": name, "removed": removed, "path": str(_HOOK_DST)}


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
