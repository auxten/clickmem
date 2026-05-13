"""Conflict detection + resolution.

When a new memory is Expanded or an existing one Revised, the storage layer
runs a vector search against the same ``(project_id, kind)`` partition. Hits
above ``CLICKMEM_CONFLICT_THRESHOLD`` (default 0.92) are inspected:

* If the candidate normalises to the same canonical form as an existing
  active memory, the call **merges** into the existing memory: the existing id
  is reused, a history row is appended, and ``recall_hits`` is incremented.
* Otherwise (embeddings close, text materially different), **both** memories
  are flagged ``status='conflicted'`` and added to each other's
  ``conflict_with`` array. The Expand / Revise call returns ``status="conflicted"``
  with the peer ids so the agent (or dashboard) can resolve.
* A non-pinned commit conflicting with a pinned memory is **rejected**; the
  caller must explicitly Revise the pinned memory.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, List

from clickmem.backend import Backend, get_backend
from clickmem.config import get_config
from clickmem.models import Memory
from clickmem.sqlutil import quote_array_str, quote_str, utc_now_sql


_log = logging.getLogger(__name__)


@dataclass
class ConflictResult:
    status: str            # "ok" | "merged" | "conflicted" | "rejected"
    id: str = ""           # final memory id (caller's, the merged peer, or "")
    peer_ids: List[str] = None  # type: ignore[assignment]
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "id": self.id,
            "peer_ids": list(self.peer_ids or []),
            "message": self.message,
        }


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def canonical(text: str) -> str:
    """Lower / strip-punct / collapse-whitespace normalisation."""
    t = (text or "").lower()
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def _fetch_neighbors(
    candidate: Memory,
    backend: Backend,
    limit: int = 8,
    threshold: float | None = None,
) -> List[dict[str, Any]]:
    if not candidate.embedding:
        return []
    threshold = float(threshold if threshold is not None else get_config().conflict_threshold)
    where_parts = [
        f"status = 'active'",
        f"project_id = {quote_str(candidate.project_id)}",
        f"kind = {quote_str(candidate.kind)}",
    ]
    if candidate.id:
        where_parts.append(f"id != {quote_str(candidate.id)}")
    where = " AND ".join(where_parts)
    rows = backend.vector_search(
        table="memories",
        query_vec=candidate.embedding,
        where=where,
        limit=int(limit),
        select="id, content, kind, project_id, privacy, pinned, status, content_hash",
    )
    return [r for r in rows if float(r.get("cosine_sim", 0.0) or 0.0) >= threshold]


def _mark_conflicted(
    candidate_id: str,
    peer_ids: Iterable[str],
    backend: Backend,
) -> None:
    arr = quote_array_str(list(peer_ids))
    sql = (
        "INSERT INTO memories "
        "(id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        "status, pinned, contract_reason, revises_id, conflict_with, content_hash, "
        "recall_hits, created_at, updated_at) "
        "SELECT id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        "'conflicted', pinned, contract_reason, revises_id, "
        f"arrayDistinct(arrayConcat(conflict_with, {arr})), content_hash, "
        f"recall_hits, created_at, {utc_now_sql()} "
        f"FROM memories FINAL WHERE id = {quote_str(candidate_id)}"
    )
    backend.execute(sql)


def _bump_recall_hits(memory_id: str, backend: Backend) -> None:
    sql = (
        "INSERT INTO memories "
        "(id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        "status, pinned, contract_reason, revises_id, conflict_with, content_hash, "
        "recall_hits, created_at, updated_at) "
        "SELECT id, content, kind, source, source_ref, project_id, privacy, tags, embedding, "
        "status, pinned, contract_reason, revises_id, conflict_with, content_hash, "
        f"recall_hits + 1, created_at, {utc_now_sql()} "
        f"FROM memories FINAL WHERE id = {quote_str(memory_id)}"
    )
    backend.execute(sql)


def check_on_commit(
    candidate: Memory,
    backend: Backend | None = None,
    threshold: float | None = None,
) -> ConflictResult:
    """Run conflict detection for a not-yet-persisted ``candidate`` memory.

    Caller (``memories.add`` / ``memories.edit``) only persists ``candidate``
    when this returns ``status='ok'``. For ``'merged'`` the caller skips the
    insert. For ``'conflicted'`` the caller writes the candidate normally and
    then this function flags both peers.
    """
    backend = backend or get_backend()
    neighbours = _fetch_neighbors(candidate, backend, limit=8, threshold=threshold)
    if not neighbours:
        return ConflictResult(status="ok", id=candidate.id, peer_ids=[])

    candidate_canon = canonical(candidate.content)

    for n in neighbours:
        peer_id = str(n.get("id", ""))
        peer_text = str(n.get("content", ""))
        peer_pinned = bool(n.get("pinned", 0))
        peer_canon = canonical(peer_text)

        if peer_canon and peer_canon == candidate_canon:
            _bump_recall_hits(peer_id, backend)
            return ConflictResult(
                status="merged",
                id=peer_id,
                peer_ids=[peer_id],
                message="merged into existing memory with identical canonical form",
            )

        if peer_pinned and not candidate.pinned:
            return ConflictResult(
                status="rejected",
                id=peer_id,
                peer_ids=[peer_id],
                message="conflicts with a pinned memory; revise that memory explicitly",
            )

    peer_ids = [str(n.get("id", "")) for n in neighbours]
    return ConflictResult(
        status="conflicted",
        id=candidate.id,
        peer_ids=peer_ids,
        message=f"conflicts with {len(peer_ids)} existing memorie(s)",
    )


def list_conflicts(
    project_id: str | None = None,
    limit: int = 200,
    backend: Backend | None = None,
) -> List[dict[str, Any]]:
    """Return active conflict groups with their peers."""
    backend = backend or get_backend()
    where = ["status = 'conflicted'"]
    if project_id:
        where.append(f"project_id = {quote_str(project_id)}")
    clause = "WHERE " + " AND ".join(where)
    sql = (
        "SELECT id, content, kind, project_id, privacy, conflict_with, "
        "toString(updated_at) AS updated_at "
        f"FROM memories FINAL {clause} ORDER BY updated_at DESC LIMIT {int(limit)}"
    )
    rows = backend.query(sql)
    return rows


def resolve(
    memory_id: str,
    op: str,
    peer_id: str = "",
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Resolve a conflict.

    Ops:
        - ``allow``: clear status on the row (and its peers if listed); keep both.
        - ``contract`` (a.k.a. ``contract_peer``): contract the ``peer_id``,
          keep ``memory_id`` active.
        - ``revise``: caller should already have edited ``memory_id``; this
          contracts ``peer_id`` and clears status on ``memory_id``.
    """
    from clickmem.memories import _set_status, forget  # local import to avoid cycle

    backend = backend or get_backend()
    op = (op or "").lower()
    if op == "allow":
        _set_status(memory_id, "active", backend=backend, clear_conflict_with=True)
        if peer_id:
            _set_status(peer_id, "active", backend=backend, clear_conflict_with=True)
        return {"status": "ok", "op": "allow", "id": memory_id, "peer_id": peer_id}
    if op in ("contract", "contract_peer"):
        if not peer_id:
            raise ValueError("resolve op=contract requires peer_id")
        forget(peer_id, reason=f"resolved conflict with {memory_id}", backend=backend)
        _set_status(memory_id, "active", backend=backend, clear_conflict_with=True)
        return {"status": "ok", "op": "contract", "id": memory_id, "peer_id": peer_id}
    if op == "revise":
        if peer_id:
            forget(peer_id, reason=f"superseded by revise of {memory_id}", backend=backend)
        _set_status(memory_id, "active", backend=backend, clear_conflict_with=True)
        return {"status": "ok", "op": "revise", "id": memory_id, "peer_id": peer_id}
    raise ValueError(f"unknown resolve op: {op!r}")
