"""Aider adapter — doc-only.

Aider does not expose a stable hook surface; we read ``.aider.chat.history.md``
files relative to the cwd and any ``CONVENTIONS.md`` style doc files. Raw
hooks are intentionally not implemented — call ``install_hooks`` raises a
clear ``NotImplementedError``.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import (
    RawSession,
    home,
    read_text,
    safe_mtime,
)

name = "aider"
label = "Aider"
experimental = False

_USER_AIDER_DIR = home() / ".aider"


def detect() -> bool:
    if _USER_AIDER_DIR.is_dir():
        return True
    cwd = Path(os.getcwd())
    return any((cwd / fname).is_file() for fname in (".aider.chat.history.md", ".aider.input.history"))


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    candidates: list[Path] = []
    cwd = Path(os.getcwd())
    for fname in (".aider.chat.history.md",):
        p = cwd / fname
        if p.is_file():
            candidates.append(p)
    if _USER_AIDER_DIR.is_dir():
        candidates.extend(Path(p) for p in glob.glob(str(_USER_AIDER_DIR / "*.md")))
    for path in candidates:
        if since is not None and safe_mtime(path) < since:
            continue
        text = read_text(path)
        if len(text) < 50:
            continue
        yield RawSession(
            session_id=path.stem,
            agent=name,
            text=text,
            cwd=str(cwd),
            meta={"path": str(path)},
        )


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    cwd = Path(os.getcwd())
    for fname in ("CONVENTIONS.md", "AIDER.md"):
        p = cwd / fname
        if p.is_file():
            paths.append(p)
    return paths


def install_hooks(server_url: str = "") -> dict[str, Any]:
    raise NotImplementedError(
        "aider adapter is doc-only: no stable hook surface upstream. "
        "Use `clickmem import-docs` to ingest CONVENTIONS.md / chat history."
    )


def uninstall_hooks() -> dict[str, Any]:
    return {"ok": True, "installed": False, "agent": name, "message": "doc-only adapter"}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items = []
    for fname in (".aider.chat.history.md",):
        p = Path(os.getcwd()) / fname
        if p.is_file():
            items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}
