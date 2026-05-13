"""Git-aware doc importer for ``AGENTS.md`` / ``CLAUDE.md`` / ``.cursor/rules/*.mdc``.

Skip rules (per the plan):

1. File > 8 KB *and* has no ``git log -- <file>`` history → AI-generated, never
   edited, drop it.
2. File contains ``<!-- generated -->`` marker → drop.
3. File contains a top-level ``## Reasoning`` block (Dream auto-memory shape)
   → drop.
4. File is bullet-heavy (> 80 % bullet lines) with avg bullet length > 200
   chars → AI-noise pattern, drop.

For ``AGENTS.md`` we explode the file into one memory per bullet point so
the dashboard can revise/contract individual principles. Every other file is
imported as a single ``kind='doc'`` memory.

Idempotency: ``source_ref = "<repo>:<relpath>:<git_sha>"``. If a memory with
the exact same ``source_ref`` already exists we skip; if the path matches
but the sha changed, the import calls :func:`memories.edit` so the dashboard
shows the diff.
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

from clickmem import memories as memories_mod
from clickmem.backend import Backend, get_backend
from clickmem.embedding import embed
from clickmem.events import write as event_write
from clickmem.projects import detect_from_cwd, upsert as project_upsert
from clickmem.sqlutil import quote_str


_log = logging.getLogger(__name__)

_MAX_NO_HISTORY_SIZE = 8 * 1024
_GENERATED_MARKER = "<!-- generated -->"
_REASONING_RE = re.compile(r"^##\s+Reasoning\b", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+\S")


@dataclass
class DocItem:
    """One importable file → one or more memories."""

    repo: str
    relpath: str
    abs_path: Path
    content: str
    git_sha: str
    has_history: bool


@dataclass
class ImportPlan:
    """The set of files we plan to ingest, with diagnostics for the user."""

    repo_root: Path
    project_id: str
    accepted: List[DocItem]
    skipped: List[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "project_id": self.project_id,
            "accepted": [{"path": str(d.abs_path), "git_sha": d.git_sha} for d in self.accepted],
            "skipped": self.skipped,
        }


# ---------- Walkers --------------------------------------------------------


def _walk_paths(root: Path) -> List[Path]:
    out: list[Path] = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        candidate = root / name
        if candidate.is_file():
            out.append(candidate)
    out.extend(Path(p) for p in glob.glob(str(root / ".cursor" / "rules" / "*.mdc")))
    out.extend(Path(p) for p in glob.glob(str(root / ".cursor" / "rules" / "*.md")))
    out.extend(Path(p) for p in glob.glob(str(root / ".claude" / "projects" / "*" / "memory" / "*.md")))
    return out


# ---------- Git helpers ----------------------------------------------------


def _git_root(start: Path) -> Path:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
        )
        return Path(out.decode("utf-8").strip())
    except Exception:
        return start


def _git_blob_sha(repo_root: Path, rel: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", f"HEAD:{rel}"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8").strip()
    except Exception:
        return ""


def _git_has_history(repo_root: Path, rel: str) -> bool:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "log", "--oneline", "--", rel],
            stderr=subprocess.DEVNULL,
        )
        return bool(out.decode("utf-8").strip())
    except Exception:
        return False


def _repo_label(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
        )
        url = out.decode("utf-8").strip()
        if url:
            return url
    except Exception:
        pass
    return f"file://{repo_root}"


# ---------- AI-noise detection --------------------------------------------


def _is_ai_noise(content: str) -> tuple[bool, str]:
    """Return ``(skip, reason)``. ``reason`` is empty when we accept."""
    if _GENERATED_MARKER in content:
        return True, "generated marker"
    if _REASONING_RE.search(content):
        return True, "Dream Reasoning block"
    lines = [ln for ln in content.splitlines() if ln.strip()]
    if not lines:
        return True, "empty"
    bullets = [ln for ln in lines if _BULLET_RE.match(ln)]
    if len(bullets) >= 5 and len(bullets) / len(lines) > 0.8:
        avg = sum(len(b) for b in bullets) / max(1, len(bullets))
        if avg > 200:
            return True, f"bullet-noise (avg={avg:.0f} chars/bullet)"
    return False, ""


# ---------- Plan ----------------------------------------------------------


def plan(repo_root: Path | str | None = None) -> ImportPlan:
    """Scan ``repo_root`` for importable docs; classify accept/skip."""
    root = Path(repo_root or os.getcwd()).expanduser().resolve()
    actual_root = _git_root(root)
    repo_label = _repo_label(actual_root)
    project = detect_from_cwd(actual_root)

    accepted: list[DocItem] = []
    skipped: list[dict[str, Any]] = []

    for path in _walk_paths(actual_root):
        try:
            rel = str(path.resolve().relative_to(actual_root))
        except ValueError:
            rel = path.name
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            skipped.append({"path": str(path), "reason": f"unreadable: {e}"})
            continue

        size = len(content.encode("utf-8"))
        has_hist = _git_has_history(actual_root, rel)
        if size > _MAX_NO_HISTORY_SIZE and not has_hist:
            skipped.append({"path": str(path), "reason": f"large no-history file ({size} bytes)"})
            continue

        skip, why = _is_ai_noise(content)
        if skip:
            skipped.append({"path": str(path), "reason": why})
            continue

        sha = _git_blob_sha(actual_root, rel)
        if not sha:
            sha = hashlib.sha1(content.encode("utf-8")).hexdigest()
        accepted.append(
            DocItem(
                repo=repo_label,
                relpath=rel,
                abs_path=path,
                content=content,
                git_sha=sha,
                has_history=has_hist,
            )
        )

    return ImportPlan(repo_root=actual_root, project_id=project.id, accepted=accepted, skipped=skipped)


# ---------- Parsing AGENTS.md bullets -------------------------------------


def _parse_agents_md(content: str) -> List[dict[str, str]]:
    """Each bullet becomes one principle memory, tagged by ``## Section``."""
    out: list[dict[str, str]] = []
    section = ""
    for line in content.split("\n"):
        line = line.rstrip()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if line.startswith("- ") and len(line) > 5:
            text = line[2:].strip()
            if not text or text.startswith("**") and text.endswith("**"):
                continue
            out.append({"content": text, "section": section})
    return out


# ---------- Idempotent ingest --------------------------------------------


def _existing_memory_id(source_ref: str, backend: Backend) -> str:
    rows = backend.query(
        f"SELECT id FROM memories FINAL WHERE source_ref = {quote_str(source_ref)} "
        "AND status != 'contracted' LIMIT 1"
    )
    return str(rows[0].get("id", "")) if rows else ""


def _existing_path_memory_id(repo: str, relpath: str, backend: Backend) -> str:
    """Find any prior memory whose source_ref starts with ``<repo>:<relpath>:``."""
    prefix = f"{repo}:{relpath}:"
    rows = backend.query(
        f"SELECT id, source_ref FROM memories FINAL WHERE startsWith(source_ref, {quote_str(prefix)}) "
        "AND status != 'contracted' LIMIT 1"
    )
    return str(rows[0].get("id", "")) if rows else ""


def _ingest_doc(
    item: DocItem,
    project_id: str,
    *,
    dry_run: bool,
    backend: Backend,
) -> dict[str, Any]:
    source_ref = f"{item.repo}:{item.relpath}:{item.git_sha}"
    is_agents = item.abs_path.name.lower() == "agents.md"
    # For AGENTS.md the per-bullet source_ref carries a "#<section>" suffix,
    # so a previous import shows up under a prefix match rather than exact.
    if is_agents:
        rows = backend.query(
            f"SELECT id FROM memories FINAL WHERE startsWith(source_ref, {quote_str(source_ref + '#')}) "
            "AND status != 'contracted' LIMIT 1"
        )
        if rows:
            return {"path": str(item.abs_path), "status": "skipped", "reason": "exact source_ref already present"}
    elif _existing_memory_id(source_ref, backend):
        return {"path": str(item.abs_path), "status": "skipped", "reason": "exact source_ref already present"}

    bullets = _parse_agents_md(item.content) if is_agents else []

    if dry_run:
        return {
            "path": str(item.abs_path),
            "status": "would-ingest",
            "kind": "principle-bullets" if is_agents else "doc",
            "memory_count": len(bullets) or 1,
            "source_ref": source_ref,
        }

    # bump source-ref on any prior version of this path (Revise semantics)
    prior = _existing_path_memory_id(item.repo, item.relpath, backend)

    results: list[dict[str, Any]] = []
    if is_agents and bullets:
        for entry in bullets:
            res = memories_mod.add(
                entry["content"],
                kind="principle",
                source="user_doc",
                source_ref=f"{source_ref}#{entry['section'][:48]}",
                project_id=project_id,
                privacy="public",
                tags=([entry["section"]] if entry["section"] else []),
                agent="import_docs",
                backend=backend,
            )
            results.append(res)
    else:
        if prior:
            res = memories_mod.edit(
                prior,
                content=item.content,
                kind="doc",
                privacy="public",
                project_id=project_id,
                agent="import_docs",
                backend=backend,
            )
        else:
            res = memories_mod.add(
                item.content,
                kind="doc",
                source="user_doc",
                source_ref=source_ref,
                project_id=project_id,
                privacy="public",
                tags=[item.abs_path.name],
                agent="import_docs",
                backend=backend,
            )
        results.append(res)

    return {
        "path": str(item.abs_path),
        "status": "ingested",
        "memory_count": len(results),
        "source_ref": source_ref,
        "results": results,
    }


def run_for_adapter(
    adapter,
    *,
    dry_run: bool = False,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Walk ``adapter.iter_doc_paths()`` and route each path through the importer.

    This is the entry point used by the ``POST /v1/imports/{name}/run`` REST
    endpoint so the dashboard can trigger an import per adapter without
    spawning a subprocess. Errors on individual files are caught — the call
    returns aggregate counts plus a list of per-file results.
    """
    backend = backend or get_backend()
    name = getattr(adapter, "name", "") or "unknown"
    paths = []
    try:
        paths = list(adapter.iter_doc_paths())
    except Exception as e:  # noqa: BLE001
        _log.warning("adapter %s iter_doc_paths failed: %s", name, e)

    accepted = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            skipped += 1
            results.append({"path": str(path), "status": "skipped", "reason": "not a file"})
            continue
        try:
            sub = run(path.parent, dry_run=dry_run, backend=backend)
            ingested = [r for r in sub.get("ingested", []) if r.get("status") == "ingested"]
            accepted += len(ingested)
            skipped += int(sub.get("skipped_count", 0) or 0)
            results.extend(sub.get("ingested", []))
        except Exception as e:  # noqa: BLE001
            skipped += 1
            results.append({"path": str(path), "status": "error", "error": str(e)})

    event_write(
        "adapter.import_docs",
        agent=name,
        message=f"adapter run on {len(paths)} paths",
        payload={"accepted": accepted, "skipped": skipped},
        backend=backend,
    )

    return {
        "started": True,
        "name": name,
        "files_scanned": len(paths),
        "accepted": accepted,
        "skipped": skipped,
        "results": results,
    }


def run(
    repo_root: Path | str | None = None,
    *,
    dry_run: bool = False,
    backend: Backend | None = None,
) -> dict[str, Any]:
    backend = backend or get_backend()
    p = plan(repo_root)

    # Make sure the project row exists so downstream filters work.
    if not dry_run:
        try:
            project = detect_from_cwd(p.repo_root)
            project_upsert(project, backend=backend)
        except Exception as e:  # noqa: BLE001
            _log.debug("project upsert during import_docs failed: %s", e)

    ingested: list[dict[str, Any]] = []
    for item in p.accepted:
        try:
            ingested.append(_ingest_doc(item, p.project_id, dry_run=dry_run, backend=backend))
        except Exception as e:  # noqa: BLE001
            _log.warning("ingest failed for %s: %s", item.abs_path, e)
            ingested.append({"path": str(item.abs_path), "status": "error", "error": str(e)})

    event_write(
        "doc.import",
        agent="import_docs",
        project_id=p.project_id,
        message=f"import_docs {'dry_run' if dry_run else 'run'} on {p.repo_root}",
        payload={"accepted": len(p.accepted), "skipped": len(p.skipped)},
        backend=backend,
    )

    return {
        "ok": True,
        "dry_run": dry_run,
        "repo_root": str(p.repo_root),
        "project_id": p.project_id,
        "accepted": len(p.accepted),
        "skipped_count": len(p.skipped),
        "skipped": p.skipped,
        "ingested": ingested,
    }


__all__ = ["DocItem", "ImportPlan", "plan", "run", "run_for_adapter"]
