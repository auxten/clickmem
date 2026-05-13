"""Zed adapter — doc-only.

Zed stores assistant conversations under ``~/.local/share/zed/assistant`` on
Linux and ``~/Library/Application Support/Zed/assistant`` on macOS. The
session format is binary-ish (sqlite + indexed blobs); we expose docs only
and rely on user-side ``import-docs``.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import RawSession, home, safe_mtime

name = "zed"
label = "Zed"
experimental = True


def _zed_root() -> Path:
    sys = platform.system().lower()
    if sys == "darwin":
        return home() / "Library" / "Application Support" / "Zed"
    return home() / ".local" / "share" / "zed"


def detect() -> bool:
    return _zed_root().is_dir()


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    if False:  # pragma: no cover - no usable raw-session path
        yield None  # type: ignore[misc]
    return


def iter_doc_paths() -> List[Path]:
    root = _zed_root()
    paths: list[Path] = []
    settings = root / "settings.json"
    if settings.is_file():
        paths.append(settings)
    keymap = root / "keymap.json"
    if keymap.is_file():
        paths.append(keymap)
    return paths


def install_hooks(server_url: str = "") -> dict[str, Any]:
    raise NotImplementedError(
        "zed adapter is doc-only: Zed exposes no external hook surface today."
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
