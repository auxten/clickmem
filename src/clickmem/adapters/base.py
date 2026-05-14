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
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


@dataclass
class V0ResidueItem:
    """A single piece of pre-v1 install residue surfaced by an adapter.

    The audit harness pre-seeds a fake ``$HOME`` containing every shape we
    used to write before the v1 rebuild — stale ``enabledPlugins.clickmem@local``
    keys, ``UserPromptSubmit`` / ``PostToolUse`` hooks pointing at the v0
    ``/hooks/claude-code`` endpoint, the ``~/.cursor/plugins/clickmem`` legacy
    dir, and the ``~/.clickmem/claude-plugin`` v0 install tree. ``hooks install``
    must surface these and (by default) clean them up surgically before
    writing the v1 hooks. Each finding carries a structured ``action`` so the
    caller can decide whether to edit-in-place or delete.
    """

    adapter: str
    path: str
    issue: str
    action: str  # "edit-in-place" | "rm" | "warn"
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    # Optional v0 cleanup hooks — adapters that don't have any v0 shape may
    # omit these and the registry will treat them as no-ops.
    def detect_v0_residue(self) -> List["V0ResidueItem"]: ...
    def clean_v0_residue(self, items: List["V0ResidueItem"]) -> List[dict[str, Any]]: ...


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


# ---------- v0 residue cleanup helpers ------------------------------------

# v0 shipped a curl-based ``command`` hook against ``/hooks/claude-code``.
# v1 ships ``type: http`` entries against ``/v1/recall`` + ``/v1/raw``. We
# discriminate by either (a) the deprecated endpoint string or (b) the
# ``type`` not being ``http`` — that way re-running the cleaner after a v1
# install does not flag the new hooks as residue (idempotency).
V0_LEGACY_ENDPOINT = "/hooks/claude-code"
V0_HOOK_NEEDLES = ("clickmem", "9527", V0_LEGACY_ENDPOINT)
V1_HOOK_ENDPOINTS = ("/v1/recall", "/v1/raw")


def _hook_entry_text(entry: Any) -> str:
    """Best-effort flatten of a single hook entry to a searchable string."""
    if isinstance(entry, dict):
        return " ".join(
            str(v) for k, v in entry.items() if k in ("command", "url", "script", "exec")
        )
    return str(entry or "")


def is_v0_hook_entry(entry: Any) -> bool:
    """Return True iff ``entry`` looks like a pre-v1 ClickMem hook.

    A hook is v0 if its command/url field references the legacy
    ``/hooks/claude-code`` endpoint, OR it references ``clickmem`` / ``9527``
    while NOT being a v1 ``type: http`` entry pointing at ``/v1/recall`` or
    ``/v1/raw``. This survives running the cleaner after install (v1 hooks
    are not flagged as residue on the second pass).
    """
    text = _hook_entry_text(entry)
    if not text:
        return False
    if V0_LEGACY_ENDPOINT in text:
        return True
    looks_like_clickmem = any(needle in text for needle in ("clickmem", ":9527"))
    if not looks_like_clickmem:
        return False
    # v1 hook is dict with type=="http" AND url containing /v1/recall or /v1/raw.
    if isinstance(entry, dict) and entry.get("type") == "http":
        url = str(entry.get("url", ""))
        if any(ep in url for ep in V1_HOOK_ENDPOINTS):
            return False
    return True


def utc_backup_suffix() -> str:
    """Produce a UTC ``.bak.YYYYMMDDHHMMSS`` suffix.

    Matches the migration worker's pattern. Includes microseconds-free
    seconds resolution; collisions inside the same second are vanishingly
    unlikely in cleanup paths and a second backup would still be safe.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def backup_path(original: Path) -> Path:
    return original.with_name(f"{original.name}.bak.{utc_backup_suffix()}")


def backup_file(original: Path) -> Path | None:
    """Copy ``original`` to a ``.bak.<timestamp>`` sibling. No-op for missing."""
    if not original.exists():
        return None
    dst = backup_path(original)
    while dst.exists():
        dst = backup_path(original)
    try:
        if original.is_file():
            shutil.copy2(original, dst)
        else:
            shutil.copytree(original, dst)
        return dst
    except OSError as e:
        _log.warning("adapters.backup_file failed for %s: %s", original, e)
        return None


def remove_path(p: Path) -> tuple[bool, str | None]:
    """Best-effort remove a directory, file, or symlink.

    Returns ``(existed, error_or_None)``. Symlinks are unlinked even when
    they dangle. Idempotent — missing paths return ``(False, None)``.
    """
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


__all__ = [
    "AgentAdapter",
    "RawSession",
    "V0ResidueItem",
    "V0_HOOK_NEEDLES",
    "V0_LEGACY_ENDPOINT",
    "V1_HOOK_ENDPOINTS",
    "backup_file",
    "backup_path",
    "base_url_default",
    "filter_since",
    "home",
    "is_v0_hook_entry",
    "iter_jsonl",
    "read_text",
    "remove_path",
    "safe_mtime",
    "safe_remove",
    "utc_backup_suffix",
    "write_json",
]
