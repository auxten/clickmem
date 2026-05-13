"""AgentAdapter protocol + helpers shared across the per-agent modules.

Each adapter is a self-contained module under ``clickmem.adapters`` that knows
how to:

* ``detect()`` whether the agent is installed on this host,
* ``iter_raw_sessions(since)`` yield freshly modified raw transcripts,
* ``iter_doc_paths()`` enumerate knowledge docs that ``import_docs`` may walk,
* ``install_hooks(server_url)`` write the agent-side hook config,
* ``uninstall_hooks()`` remove that config,
* ``export_blob(dst_path)`` produce a portable snapshot for offline inspection.

Adapters that have no real hook story (e.g. JetBrains AI, Cline) raise
``NotImplementedError`` from ``install_hooks`` with a clear message; the
registry treats that as "doc-only".
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Protocol, runtime_checkable

_log = logging.getLogger(__name__)


@dataclass
class RawSession:
    """A freshly observed raw session that the loop wants to POST to ``/v1/raw``."""

    session_id: str
    agent: str
    text: str
    cwd: str = ""
    project_id: str = ""
    meta: dict[str, Any] | None = None


@runtime_checkable
class AgentAdapter(Protocol):
    name: str
    label: str
    experimental: bool

    def detect(self) -> bool: ...
    def iter_raw_sessions(self, since: float | None = None) -> Iterator[RawSession]: ...
    def iter_doc_paths(self) -> List[Path]: ...
    def install_hooks(self, server_url: str) -> dict[str, Any]: ...
    def uninstall_hooks(self) -> dict[str, Any]: ...
    def export_blob(self, dst_path: Path) -> dict[str, Any]: ...


# ---------- Shared helpers ------------------------------------------------


def home() -> Path:
    return Path(os.path.expanduser("~"))


def safe_mtime(path: Path | str) -> float:
    try:
        return float(os.path.getmtime(str(path)))
    except OSError:
        return 0.0


def read_text(path: Path | str, limit: int = 1_000_000) -> str:
    try:
        with open(str(path), encoding="utf-8") as fh:
            return fh.read(limit)
    except (OSError, UnicodeDecodeError) as e:
        _log.debug("adapters.read_text failed for %s: %s", path, e)
        return ""


def iter_jsonl(path: Path | str) -> Iterator[dict[str, Any]]:
    try:
        with open(str(path), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        _log.debug("adapters.iter_jsonl failed for %s: %s", path, e)
        return


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_remove(path: Path) -> bool:
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError as e:
        _log.warning("adapters.safe_remove failed for %s: %s", path, e)
    return False


def filter_since(paths: Iterable[Path], since: float | None) -> List[Path]:
    if since is None:
        return list(paths)
    out: list[Path] = []
    for p in paths:
        if safe_mtime(p) >= since:
            out.append(p)
    return out


def base_url_default() -> str:
    host = os.environ.get("CLICKMEM_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("CLICKMEM_SERVER_PORT", "9527")
    return f"http://{host}:{port}"


__all__ = [
    "AgentAdapter",
    "RawSession",
    "base_url_default",
    "filter_since",
    "home",
    "iter_jsonl",
    "read_text",
    "safe_mtime",
    "safe_remove",
    "write_json",
]
