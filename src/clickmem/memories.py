"""Memory CRUD: Expand / Revise / Contract / Pin / Bulk.

Every write path runs through this module. Conflict detection is invoked on
``add`` and ``edit``. Each successful mutation writes a row to the events
table via :mod:`clickmem.events`.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any, Iterable, List

from clickmem.backend import Backend, get_backend
from clickmem.blacklist import enforce_on_insert
from clickmem.conflicts import ConflictResult, check_on_commit, _mark_conflicted
from clickmem.embedding import embed
from clickmem.events import write as event_write
from clickmem.history import append as history_append
from clickmem.models import (
    VALID_KINDS,
    VALID_PRIVACY,
    VALID_STATUS,
    Memory,
)
from clickmem.sqlutil import (
    quote_array_float,
    quote_array_str,
    quote_bool,
    quote_str,
    utc_now_sql,
)


_log = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex


def _hash_content(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def _coerce_kind(kind: str | None) -> str:
    k = (kind or "free").lower()
    return k if k in VALID_KINDS else "free"


def _coerce_privacy(p: str | None) -> str:
    v = (p or "private").lower()
    return v if v in VALID_PRIVACY else "private"


def _coerce_status(s: str | None) -> str:
    v = (s or "active").lower()
    return v if v in VALID_STATUS else "active"


def _insert(memory: Memory, backend: Backend) -> None:
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


def _set_status(
    memory_id: str,
    new_status: str,
    backend: Backend,
    contract_reason: str = "",
    clear_conflict_with: bool = False,
    pinned: bool | None = None,
) -> None:
    """Re-insert the latest version of a row with status/pinned changes."""
    cwith = "[]" if clear_conflict_with else "conflict_with"
    pinned_expr = quote_bool(pinned) if pinned is not None else "pinned"
    cr = quote_str(contract_reason) if contract_reason else "contract_reason"
    sql = (
        "INSERT INTO memories "
        "(id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        "status, pinned, contract_reason, revises_id, conflict_with, content_hash, "
        "recall_hits, created_at, updated_at) "
        "SELECT id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        f"{quote_str(new_status)}, {pinned_expr}, {cr}, revises_id, {cwith}, content_hash, "
        f"recall_hits, created_at, {utc_now_sql()} "
        f"FROM memories FINAL WHERE id = {quote_str(memory_id)}"
    )
    backend.execute(sql)


_SELECT = (
    "id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
    "status, pinned, contract_reason, revises_id, conflict_with, content_hash, "
    "recall_hits, toString(created_at) AS created_at, toString(updated_at) AS updated_at"
)


def get(memory_id: str, backend: Backend | None = None) -> Memory | None:
    backend = backend or get_backend()
    rows = backend.query(
        f"SELECT {_SELECT} FROM memories FINAL WHERE id = {quote_str(memory_id)} LIMIT 1"
    )
    return Memory.from_row(rows[0]) if rows else None


def add(
    content: str,
    *,
    kind: str = "free",
    source: str = "agent_remember",
    source_ref: str = "",
    project_id: str = "",
    privacy: str = "private",
    tags: Iterable[str] | None = None,
    pinned: bool = False,
    revises_id: str = "",
    agent: str = "",
    skip_conflict_check: bool = False,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Expand: commit a new memory.

    Returns ``{status, id, peer_ids, message}`` so the caller can react to
    merges / conflicts immediately.
    """
    backend = backend or get_backend()
    if not content or not content.strip():
        raise ValueError("memory content cannot be empty")
    content = content.strip()

    bl = enforce_on_insert(content, project_id=project_id, backend=backend)
    if bl is not None:
        event_write(
            "blacklist.hit",
            agent=agent,
            project_id=project_id,
            message=f"blocked Expand: {bl.pattern}",
            payload={"pattern": bl.pattern, "scope": bl.scope},
            backend=backend,
        )
        return {
            "status": "refused",
            "id": "",
            "peer_ids": [],
            "message": f"matched blacklist pattern {bl.pattern!r}",
        }

    embedding = embed(content)
    candidate = Memory(
        id=_new_id(),
        content=content,
        kind=_coerce_kind(kind),
        source=source or "agent_remember",
        source_ref=source_ref or "",
        project_id=project_id or "",
        privacy=_coerce_privacy(privacy),
        tags=list(tags or []),
        embedding=embedding,
        status="active",
        pinned=bool(pinned),
        revises_id=revises_id or "",
        content_hash=_hash_content(content),
    )

    if skip_conflict_check:
        conflict = ConflictResult(status="ok", id=candidate.id, peer_ids=[])
    else:
        conflict = check_on_commit(candidate, backend=backend)

    if conflict.status == "merged":
        history_append(
            conflict.id,
            op="expand",
            content=content,
            edited_by=agent or source,
            note="merged duplicate",
            backend=backend,
        )
        event_write(
            "memory.expand",
            agent=agent,
            project_id=project_id,
            memory_id=conflict.id,
            message="merged into existing memory",
            payload={"status": "merged"},
            backend=backend,
        )
        return {
            "status": "merged",
            "id": conflict.id,
            "peer_ids": conflict.peer_ids or [conflict.id],
            "message": conflict.message,
        }

    if conflict.status == "rejected":
        event_write(
            "memory.expand",
            agent=agent,
            project_id=project_id,
            memory_id=conflict.id,
            message="rejected by pinned conflict",
            payload={"peer_ids": conflict.peer_ids},
            backend=backend,
        )
        return {
            "status": "rejected",
            "id": "",
            "peer_ids": conflict.peer_ids or [],
            "message": conflict.message,
        }

    if conflict.status == "conflicted":
        candidate.status = "conflicted"
        candidate.conflict_with = list(conflict.peer_ids or [])

    _insert(candidate, backend)
    history_append(
        candidate.id,
        op="expand",
        content=content,
        edited_by=agent or source,
        backend=backend,
    )

    if conflict.status == "conflicted":
        _mark_conflicted(candidate.id, conflict.peer_ids or [], backend)
        for peer in conflict.peer_ids or []:
            _mark_conflicted(peer, [candidate.id], backend)
        event_write(
            "memory.expand",
            agent=agent,
            project_id=project_id,
            memory_id=candidate.id,
            message="conflict surfaced",
            payload={"peer_ids": conflict.peer_ids, "status": "conflicted"},
            backend=backend,
        )
        return {
            "status": "conflicted",
            "id": candidate.id,
            "peer_ids": conflict.peer_ids or [],
            "message": conflict.message,
        }

    event_write(
        "memory.expand",
        agent=agent,
        project_id=project_id,
        memory_id=candidate.id,
        message="memory added",
        backend=backend,
    )
    return {
        "status": "added",
        "id": candidate.id,
        "peer_ids": [],
        "message": "memory committed",
    }


def edit(
    memory_id: str,
    *,
    content: str | None = None,
    kind: str | None = None,
    privacy: str | None = None,
    project_id: str | None = None,
    tags: Iterable[str] | None = None,
    pinned: bool | None = None,
    revises_id: str | None = None,
    agent: str = "",
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Revise: edit an existing memory; re-runs conflict detection."""
    backend = backend or get_backend()
    existing = get(memory_id, backend=backend)
    if existing is None:
        return {"status": "missing", "id": memory_id, "peer_ids": [], "message": "no such memory"}

    if content is not None:
        existing.content = content.strip()
    if kind is not None:
        existing.kind = _coerce_kind(kind)
    if privacy is not None:
        existing.privacy = _coerce_privacy(privacy)
    if project_id is not None:
        existing.project_id = project_id
    if tags is not None:
        existing.tags = list(tags)
    if pinned is not None:
        existing.pinned = bool(pinned)
    if revises_id is not None:
        existing.revises_id = revises_id

    existing.status = "active"
    existing.conflict_with = []
    existing.content_hash = _hash_content(existing.content)
    existing.embedding = embed(existing.content)

    bl = enforce_on_insert(existing.content, project_id=existing.project_id, backend=backend)
    if bl is not None:
        return {
            "status": "refused",
            "id": memory_id,
            "peer_ids": [],
            "message": f"matched blacklist pattern {bl.pattern!r}",
        }

    conflict = check_on_commit(existing, backend=backend)
    if conflict.status == "merged":
        history_append(
            conflict.id,
            op="revise",
            content=existing.content,
            edited_by=agent or "user",
            prev_id=memory_id,
            note="merged duplicate on revise",
            backend=backend,
        )
        event_write(
            "memory.revise",
            agent=agent,
            project_id=existing.project_id,
            memory_id=conflict.id,
            message="revise merged with existing",
            backend=backend,
        )
        return {
            "status": "merged",
            "id": conflict.id,
            "peer_ids": conflict.peer_ids or [conflict.id],
            "message": conflict.message,
        }

    if conflict.status == "rejected":
        return {
            "status": "rejected",
            "id": memory_id,
            "peer_ids": conflict.peer_ids or [],
            "message": conflict.message,
        }

    if conflict.status == "conflicted":
        existing.status = "conflicted"
        existing.conflict_with = list(conflict.peer_ids or [])

    _insert(existing, backend)
    history_append(
        existing.id,
        op="revise",
        content=existing.content,
        edited_by=agent or "user",
        backend=backend,
    )
    if conflict.status == "conflicted":
        _mark_conflicted(existing.id, conflict.peer_ids or [], backend)
        for peer in conflict.peer_ids or []:
            _mark_conflicted(peer, [existing.id], backend)

    event_write(
        "memory.revise",
        agent=agent,
        project_id=existing.project_id,
        memory_id=existing.id,
        message="memory revised",
        payload={"conflict_status": conflict.status},
        backend=backend,
    )
    return {
        "status": "conflicted" if conflict.status == "conflicted" else "edited",
        "id": existing.id,
        "peer_ids": conflict.peer_ids or [],
        "message": conflict.message or "memory edited",
    }


def forget(
    memory_id: str,
    *,
    reason: str = "",
    agent: str = "",
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Contract: mark a memory ``status='contracted'``. Recall ignores these."""
    backend = backend or get_backend()
    existing = get(memory_id, backend=backend)
    if existing is None:
        return {"status": "missing", "id": memory_id, "message": "no such memory"}
    _set_status(
        memory_id, "contracted", backend=backend, contract_reason=reason, clear_conflict_with=True
    )
    history_append(
        memory_id, op="contract", content=existing.content, edited_by=agent or "user",
        note=reason, backend=backend,
    )
    event_write(
        "memory.contract",
        agent=agent,
        project_id=existing.project_id,
        memory_id=memory_id,
        message=reason or "memory contracted",
        backend=backend,
    )
    return {"status": "contracted", "id": memory_id, "message": reason}


def pin(memory_id: str, *, agent: str = "", backend: Backend | None = None) -> dict[str, Any]:
    backend = backend or get_backend()
    existing = get(memory_id, backend=backend)
    if existing is None:
        return {"status": "missing", "id": memory_id}
    _set_status(memory_id, "active", backend=backend, pinned=True, clear_conflict_with=True)
    history_append(memory_id, op="pin", content=existing.content, edited_by=agent or "user", backend=backend)
    event_write("memory.pin", agent=agent, project_id=existing.project_id, memory_id=memory_id, backend=backend)
    return {"status": "pinned", "id": memory_id}


def unpin(memory_id: str, *, agent: str = "", backend: Backend | None = None) -> dict[str, Any]:
    backend = backend or get_backend()
    existing = get(memory_id, backend=backend)
    if existing is None:
        return {"status": "missing", "id": memory_id}
    _set_status(memory_id, existing.status, backend=backend, pinned=False)
    history_append(memory_id, op="unpin", content=existing.content, edited_by=agent or "user", backend=backend)
    event_write("memory.unpin", agent=agent, project_id=existing.project_id, memory_id=memory_id, backend=backend)
    return {"status": "unpinned", "id": memory_id}


def list_paginated(
    *,
    project_id: str | None = None,
    privacy: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    pinned: bool | None = None,
    source: str | None = None,
    search: str | None = None,
    offset: int = 0,
    limit: int = 50,
    order: str = "updated_at DESC",
    backend: Backend | None = None,
) -> dict[str, Any]:
    backend = backend or get_backend()
    where: list[str] = []
    if project_id is not None and project_id != "*":
        where.append(f"project_id = {quote_str(project_id)}")
    if privacy:
        where.append(f"privacy = {quote_str(privacy)}")
    if kind:
        where.append(f"kind = {quote_str(kind)}")
    if status:
        where.append(f"status = {quote_str(status)}")
    if pinned is not None:
        where.append(f"pinned = {quote_bool(pinned)}")
    if source:
        where.append(f"source = {quote_str(source)}")
    if search:
        where.append(f"positionCaseInsensitive(content, {quote_str(search)}) > 0")
    clause = "WHERE " + " AND ".join(where) if where else ""
    total_rows = backend.query(f"SELECT count() AS c FROM memories FINAL {clause}")
    total = int(total_rows[0]["c"]) if total_rows else 0
    rows = backend.query(
        f"SELECT {_SELECT} FROM memories FINAL {clause} ORDER BY {order} "
        f"LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    return {
        "total": total,
        "offset": int(offset),
        "limit": int(limit),
        "items": [Memory.from_row(r).to_dict() for r in rows],
    }


def bulk(
    ids: List[str],
    op: str,
    *,
    payload: dict[str, Any] | None = None,
    agent: str = "",
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Apply ``op`` to every id. Supported ops:

        - ``pin`` / ``unpin``
        - ``forget`` with optional ``reason``
        - ``set_privacy`` with ``privacy``
        - ``set_project`` with ``project_id``
        - ``blacklist`` (adds ``id:<uuid>`` patterns)
    """
    from clickmem.blacklist import add as blacklist_add  # avoid top-level cycle

    backend = backend or get_backend()
    payload = payload or {}
    op = (op or "").lower()
    results: list[dict[str, Any]] = []
    for mid in ids:
        try:
            if op == "pin":
                results.append(pin(mid, agent=agent, backend=backend))
            elif op == "unpin":
                results.append(unpin(mid, agent=agent, backend=backend))
            elif op == "forget":
                results.append(forget(mid, reason=str(payload.get("reason", "")), agent=agent, backend=backend))
            elif op == "set_privacy":
                results.append(edit(mid, privacy=str(payload.get("privacy", "private")), agent=agent, backend=backend))
            elif op == "set_project":
                results.append(edit(mid, project_id=str(payload.get("project_id", "")), agent=agent, backend=backend))
            elif op == "blacklist":
                bl = blacklist_add(
                    pattern=f"id:{mid}",
                    scope=payload.get("scope", "global"),
                    reason=payload.get("reason", "bulk blacklist"),
                    backend=backend,
                )
                forget(mid, reason="bulk blacklist", agent=agent, backend=backend)
                results.append({"status": "blacklisted", "id": mid, "blacklist_id": bl.id})
            else:
                results.append({"status": "unknown_op", "id": mid, "op": op})
        except Exception as e:  # noqa: BLE001
            _log.exception("bulk op %s failed for %s", op, mid)
            results.append({"status": "error", "id": mid, "error": str(e)})
    event_write("memory.bulk", agent=agent, message=f"bulk {op} on {len(ids)} ids", payload={"op": op}, backend=backend)
    return {"op": op, "count": len(results), "results": results}


def neighbors(memory_id: str, *, limit: int = 10, backend: Backend | None = None) -> List[dict[str, Any]]:
    """Return memories closest to ``memory_id`` (used by the dashboard drawer)."""
    backend = backend or get_backend()
    target = get(memory_id, backend=backend)
    if target is None or not target.embedding:
        return []
    where = f"id != {quote_str(memory_id)} AND status != 'contracted'"
    rows = backend.vector_search(
        table="memories",
        query_vec=target.embedding,
        where=where,
        limit=int(limit),
        select="id, content, kind, project_id, privacy, status, pinned",
    )
    return rows
