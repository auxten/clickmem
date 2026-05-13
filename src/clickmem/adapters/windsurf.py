"""Windsurf adapter — doc-only.

Windsurf maintains ``.windsurfrules`` and ``global_rules.md`` files; the
chat history is held inside the editor with no documented persistent path.
Hook install is not implemented.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import RawSession, home, safe_mtime

name = "windsurf"
label = "Windsurf"
experimental = True

_RULES_GLOBAL = home() / ".codeium" / "windsurf" / "memories"


def detect() -> bool:
    if _RULES_GLOBAL.is_dir():
        return True
    cwd = Path(os.getcwd())
    return (cwd / ".windsurfrules").is_file()


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    if False:  # pragma: no cover - no raw-session surface yet
        yield None  # type: ignore[misc]
    return


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    cwd = Path(os.getcwd())
    for fname in (".windsurfrules", "global_rules.md"):
        p = cwd / fname
        if p.is_file():
            paths.append(p)
    if _RULES_GLOBAL.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(_RULES_GLOBAL / "*.md")))
    return paths


def install_hooks(server_url: str = "") -> dict[str, Any]:
    raise NotImplementedError(
        "windsurf adapter is doc-only: Windsurf has no documented hook surface. "
        "Use `clickmem import-docs` to ingest .windsurfrules and global memories."
    )


def uninstall_hooks() -> dict[str, Any]:
    return {"ok": True, "installed": False, "agent": name, "message": "doc-only adapter"}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for p in iter_doc_paths():
        items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items, "experimental": True}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path), "experimental": True}
