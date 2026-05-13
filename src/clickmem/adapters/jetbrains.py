"""JetBrains AI Assistant adapter — experimental, doc-only.

JetBrains stores its AI configuration under
``~/Library/Application Support/JetBrains/<IDE><version>/options/`` (macOS)
or ``~/.config/JetBrains/<IDE><version>/options/`` (Linux). The
``ai.assistant.xml`` file is XML; we surface it as a doc path for users to
manually review. Hook install is not implemented upstream.
"""

from __future__ import annotations

import glob
import json
import logging
import platform
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import RawSession, home, safe_mtime

_log = logging.getLogger(__name__)

name = "jetbrains"
label = "JetBrains AI"
experimental = True


def _candidate_roots() -> list[Path]:
    sys = platform.system().lower()
    if sys == "darwin":
        return [home() / "Library" / "Application Support" / "JetBrains"]
    if sys.startswith("linux"):
        return [home() / ".config" / "JetBrains"]
    return [home() / "AppData" / "Roaming" / "JetBrains"]


def detect() -> bool:
    found = any(p.is_dir() for p in _candidate_roots())
    if found:
        _log.info("clickmem.adapters.jetbrains: experimental adapter; XML schema may change between IDE builds")
    return found


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    if False:  # pragma: no cover - no raw transcript path
        yield None  # type: ignore[misc]
    return


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    for root in _candidate_roots():
        if not root.is_dir():
            continue
        for ide in root.iterdir():
            opts = ide / "options"
            if opts.is_dir():
                paths.extend(Path(p) for p in glob.glob(str(opts / "ai.assistant.xml")))
    return paths


def install_hooks(server_url: str = "") -> dict[str, Any]:
    raise NotImplementedError(
        "jetbrains adapter is experimental and doc-only: JetBrains AI has no public hook surface."
    )


def uninstall_hooks() -> dict[str, Any]:
    return {"ok": True, "installed": False, "agent": name, "message": "doc-only adapter"}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items = []
    for p in iter_doc_paths():
        items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items, "experimental": True}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path), "experimental": True}
