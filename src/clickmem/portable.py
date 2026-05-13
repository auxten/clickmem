"""Portable export + import: JSONL and Markdown bundles.

The JSONL format is the canonical one. Each line is a memory object plus
``embedding`` (so an offline machine can reuse them without re-embedding).
The first line of every JSONL bundle is a manifest header:

    {"clickmem_export": "1.0", "exported_at": "...", "count": N, "filter": {...}}

Markdown format is a human-readable bundle: a top-level header followed by
one ``## <id>`` section per memory with metadata and the body. Imports are
JSONL-only — Markdown is meant for review/sharing.

Idempotency on import: skip a row whose ``(content_hash, project_id)``
already exists in the active set.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List

from clickmem import memories as memories_mod
from clickmem.backend import Backend, get_backend
from clickmem.events import write as event_write
from clickmem.models import Memory
from clickmem.sqlutil import quote_array_float, quote_array_str, quote_bool, quote_str, utc_now_sql


_log = logging.getLogger(__name__)

MANIFEST_KEY = "clickmem_export"
MANIFEST_VERSION = "1.0"


# ---------- Export --------------------------------------------------------


def _select_rows(
    *,
    project_id: str | None = None,
    privacy: str | None = None,
    since: str | None = None,
    backend: Backend | None = None,
) -> List[Memory]:
    backend = backend or get_backend()
    where: list[str] = ["status != 'contracted'"]
    if project_id is not None and project_id != "*":
        where.append(f"project_id = {quote_str(project_id)}")
    if privacy:
        where.append(f"privacy = {quote_str(privacy)}")
    if since:
        where.append(f"updated_at >= parseDateTime64BestEffortOrNull({quote_str(since)})")
    clause = "WHERE " + " AND ".join(where)
    sql = (
        "SELECT id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        "status, pinned, contract_reason, revises_id, conflict_with, content_hash, recall_hits, "
        "toString(created_at) AS created_at, toString(updated_at) AS updated_at "
        f"FROM memories FINAL {clause} ORDER BY updated_at DESC"
    )
    rows = backend.query(sql)
    return [Memory.from_row(r) for r in rows]


def export_jsonl(
    out_path: Path,
    *,
    project_id: str | None = None,
    privacy: str | None = None,
    since: str | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    rows = _select_rows(project_id=project_id, privacy=privacy, since=since, backend=backend)
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        MANIFEST_KEY: MANIFEST_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "count": len(rows),
        "filter": {"project_id": project_id, "privacy": privacy, "since": since},
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False) + "\n")
        for memory in rows:
            fh.write(json.dumps(memory.to_dict(include_embedding=True), ensure_ascii=False) + "\n")
    event_write(
        "export.jsonl",
        message=f"exported {len(rows)} memories to {out_path}",
        payload={"path": str(out_path), "count": len(rows)},
    )
    return {"ok": True, "path": str(out_path), "count": len(rows), "manifest": manifest}


def export_markdown(
    out_path: Path,
    *,
    project_id: str | None = None,
    privacy: str | None = None,
    since: str | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    rows = _select_rows(project_id=project_id, privacy=privacy, since=since, backend=backend)
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("# ClickMem export\n\n")
        fh.write(f"- exported_at: {datetime.now(timezone.utc).isoformat()}\n")
        fh.write(f"- count: {len(rows)}\n")
        fh.write(f"- project_id: {project_id or '(all)'}\n")
        fh.write(f"- privacy: {privacy or '(all)'}\n")
        fh.write(f"- since: {since or '(beginning)'}\n\n")
        for memory in rows:
            fh.write(f"## {memory.id}\n\n")
            tags = ", ".join(memory.tags) if memory.tags else ""
            fh.write(
                f"- kind: `{memory.kind}` | project: `{memory.project_id or '(global)'}` | "
                f"privacy: `{memory.privacy}` | pinned: `{memory.pinned}` | status: `{memory.status}`\n"
            )
            if tags:
                fh.write(f"- tags: {tags}\n")
            if memory.source_ref:
                fh.write(f"- source_ref: `{memory.source_ref}`\n")
            fh.write("\n")
            fh.write(memory.content)
            fh.write("\n\n---\n\n")
    event_write(
        "export.markdown",
        message=f"exported {len(rows)} memories to {out_path}",
        payload={"path": str(out_path), "count": len(rows)},
    )
    return {"ok": True, "path": str(out_path), "count": len(rows)}


# ---------- Import (JSONL only) ------------------------------------------


def _hash_content(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _existing_hashes(project_id: str, backend: Backend) -> set[str]:
    rows = backend.query(
        "SELECT DISTINCT content_hash FROM memories FINAL "
        f"WHERE project_id = {quote_str(project_id)} AND status != 'contracted'"
    )
    return {str(r.get("content_hash", "")) for r in rows if r.get("content_hash")}


def _raw_insert(memory: Memory, backend: Backend) -> None:
    """Insert a memory verbatim, preserving id / embedding / status / timestamps."""
    sql = (
        "INSERT INTO memories ("
        "id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        "status, pinned, contract_reason, revises_id, conflict_with, "
        "content_hash, recall_hits, created_at, updated_at"
        ") VALUES ("
        f"{quote_str(memory.id)}, {quote_str(memory.content)}, {quote_str(memory.kind)}, "
        f"{quote_str(memory.source)}, {quote_str(memory.source_ref)}, "
        f"{quote_str(memory.project_id)}, {quote_str(memory.privacy)}, "
        f"{quote_array_str(memory.tags)}, {quote_array_float(memory.embedding)}, "
        f"{quote_str(memory.status)}, {quote_bool(memory.pinned)}, "
        f"{quote_str(memory.contract_reason)}, {quote_str(memory.revises_id)}, "
        f"{quote_array_str(memory.conflict_with)}, {quote_str(memory.content_hash)}, "
        f"{int(memory.recall_hits)}, {utc_now_sql()}, {utc_now_sql()}"
        ")"
    )
    backend.execute(sql)


def import_jsonl(
    src_path: Path,
    *,
    re_embed: bool = False,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Re-ingest a bundle produced by :func:`export_jsonl`.

    Dedup key: ``(content_hash, project_id)``. Rows whose hash already exists
    in the same project are skipped. Embeddings ride along in the bundle; pass
    ``re_embed=True`` to recompute with the local model.
    """
    backend = backend or get_backend()
    src = Path(src_path).expanduser().resolve()
    if not src.is_file():
        return {"ok": False, "error": f"file not found: {src}"}

    cache: dict[str, set[str]] = {}
    ingested = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}

    with open(src, encoding="utf-8") as fh:
        for ln, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append({"line": ln, "error": f"invalid json: {e}"})
                continue
            if ln == 1 and isinstance(obj, dict) and obj.get(MANIFEST_KEY):
                manifest = obj
                continue
            try:
                memory = _coerce(obj)
            except Exception as e:  # noqa: BLE001
                errors.append({"line": ln, "error": f"coerce failed: {e}"})
                continue
            if not memory.content_hash:
                memory.content_hash = _hash_content(memory.content)

            key = memory.project_id
            if key not in cache:
                cache[key] = _existing_hashes(key, backend)
            if memory.content_hash in cache[key]:
                skipped += 1
                continue

            if re_embed or not memory.embedding:
                from clickmem.embedding import embed

                try:
                    memory.embedding = embed(memory.content)
                except Exception as e:  # noqa: BLE001
                    _log.debug("re-embed failed on import line %s: %s", ln, e)

            try:
                _raw_insert(memory, backend)
                cache[key].add(memory.content_hash)
                ingested += 1
            except Exception as e:  # noqa: BLE001
                errors.append({"line": ln, "error": f"insert failed: {e}"})

    event_write(
        "import.jsonl",
        message=f"imported {ingested} memories from {src}",
        payload={"path": str(src), "ingested": ingested, "skipped": skipped, "errors": len(errors)},
    )

    return {
        "ok": True,
        "path": str(src),
        "manifest": manifest,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
    }


def _coerce(obj: dict[str, Any]) -> Memory:
    """Re-hydrate a memory dict from an export bundle into a :class:`Memory`."""
    return Memory(
        id=str(obj.get("id", "")),
        content=str(obj.get("content", "")),
        kind=str(obj.get("kind", "free")) or "free",
        source=str(obj.get("source", "import")) or "import",
        source_ref=str(obj.get("source_ref", "")),
        project_id=str(obj.get("project_id", "")),
        privacy=str(obj.get("privacy", "private")) or "private",
        tags=list(obj.get("tags") or []),
        embedding=[float(x) for x in (obj.get("embedding") or [])],
        status=str(obj.get("status", "active")) or "active",
        pinned=bool(obj.get("pinned", False)),
        contract_reason=str(obj.get("contract_reason", "")),
        revises_id=str(obj.get("revises_id", "")),
        conflict_with=list(obj.get("conflict_with") or []),
        content_hash=str(obj.get("content_hash", "")),
        recall_hits=int(obj.get("recall_hits", 0) or 0),
    )


__all__ = ["export_jsonl", "export_markdown", "import_jsonl"]
