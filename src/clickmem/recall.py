"""Embedding-only recall with project + privacy scoring.

Scoring (no LLM in the loop, ever):

    score = cosine_sim * project_multiplier

Where ``project_multiplier`` is:

    - 1.0 for same-project (``hit.project_id == query.project_id``)
    - 0.9 for global   (``hit.project_id == ''``)
    - 0.0 for other-project unless ``cross_project=True`` or the project pair
      is whitelisted via ``projects.link()``.

Privacy filter (default ``include_confidential=False``):

    - returns ``public`` and ``private`` rows
    - ``confidential`` rows are excluded unless the caller opts in via
      ``include_confidential=True`` (the MCP layer further requires the agent
      to pass ``privacy_ack=true`` before doing so).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List

from clickmem.backend import Backend, get_backend
from clickmem.blacklist import enforce_on_recall
from clickmem.embedding import embed
from clickmem.events import write as event_write
from clickmem.projects import allowed_cross_refs
from clickmem.sqlutil import quote_str


_log = logging.getLogger(__name__)


@dataclass
class Hit:
    id: str
    content: str
    kind: str
    project_id: str
    privacy: str
    status: str
    pinned: bool
    cosine_sim: float
    score: float
    project_boost: float
    source: str = ""
    tags: List[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "kind": self.kind,
            "project_id": self.project_id,
            "privacy": self.privacy,
            "status": self.status,
            "pinned": self.pinned,
            "cosine_sim": float(self.cosine_sim),
            "score": float(self.score),
            "project_boost": float(self.project_boost),
            "source": self.source,
            "tags": list(self.tags),
            "updated_at": self.updated_at,
        }


def _project_multiplier(
    hit_project: str,
    query_project: str,
    cross_project: bool,
    backend: Backend,
) -> float:
    if hit_project == query_project:
        return 1.0
    if hit_project == "":
        return 0.9
    if cross_project:
        return 1.0
    if query_project and allowed_cross_refs(query_project, hit_project, backend=backend):
        return 1.0
    return 0.0


def recall(
    query: str,
    *,
    project_id: str = "",
    limit: int = 10,
    include_confidential: bool = False,
    cross_project: bool = False,
    kind: str | None = None,
    pool_size: int = 50,
    agent: str = "",
    backend: Backend | None = None,
) -> List[Hit]:
    """Return ranked memories for ``query``. No LLM is invoked."""
    backend = backend or get_backend()
    if not query or not query.strip():
        return []
    vec = embed(query.strip())

    where_parts = ["status = 'active'", "pending_embedding = 0"]
    if not include_confidential:
        where_parts.append("privacy != 'confidential'")
    if kind:
        where_parts.append(f"kind = {quote_str(kind)}")
    where = " AND ".join(where_parts)

    rows = backend.vector_search(
        table="memories",
        query_vec=vec,
        where=where,
        limit=int(max(pool_size, limit * 4)),
        select=(
            "id, content, kind, project_id, privacy, status, pinned, source, tags, "
            "toString(updated_at) AS updated_at"
        ),
    )

    rows = enforce_on_recall(rows, project_id=project_id, backend=backend)

    hits: List[Hit] = []
    for r in rows:
        mult = _project_multiplier(str(r.get("project_id", "")), project_id, cross_project, backend)
        if mult <= 0.0:
            continue
        cosine = float(r.get("cosine_sim", 0.0) or 0.0)
        hits.append(
            Hit(
                id=str(r.get("id", "")),
                content=str(r.get("content", "")),
                kind=str(r.get("kind", "free")),
                project_id=str(r.get("project_id", "")),
                privacy=str(r.get("privacy", "private")),
                status=str(r.get("status", "active")),
                pinned=bool(r.get("pinned", 0)),
                cosine_sim=cosine,
                project_boost=mult,
                score=cosine * mult,
                source=str(r.get("source", "")),
                tags=list(r.get("tags") or []),
                updated_at=str(r.get("updated_at", "")),
            )
        )

    hits.sort(key=lambda h: (h.pinned, h.score), reverse=True)
    out = hits[: int(limit)]

    event_write(
        "recall.run",
        agent=agent,
        project_id=project_id,
        message=f"recall returned {len(out)} hits",
        payload={"query_preview": query[:120], "hit_ids": [h.id for h in out]},
        backend=backend,
    )
    return out


def recall_trace(
    query: str,
    *,
    project_id: str = "",
    limit: int = 10,
    include_confidential: bool = False,
    cross_project: bool = False,
    kind: str | None = None,
    pool_size: int = 50,
    agent: str = "",
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Return scoring breakdown alongside the hits for the Recall Lab page."""
    backend = backend or get_backend()
    if not query or not query.strip():
        return {"query": query, "hits": [], "candidates": [], "filters": {}}

    vec = embed(query.strip())

    where_parts = ["status = 'active'", "pending_embedding = 0"]
    if not include_confidential:
        where_parts.append("privacy != 'confidential'")
    if kind:
        where_parts.append(f"kind = {quote_str(kind)}")
    where = " AND ".join(where_parts)

    rows = backend.vector_search(
        table="memories",
        query_vec=vec,
        where=where,
        limit=int(max(pool_size, limit * 4)),
        select=(
            "id, content, kind, project_id, privacy, status, pinned, source, tags, "
            "toString(updated_at) AS updated_at"
        ),
    )

    pre_filter = list(rows)
    rows_after_bl = enforce_on_recall(rows, project_id=project_id, backend=backend)
    rejected_ids = {str(r.get("id", "")) for r in pre_filter} - {str(r.get("id", "")) for r in rows_after_bl}

    candidates: List[dict[str, Any]] = []
    hits: List[Hit] = []
    for r in pre_filter:
        rid = str(r.get("id", ""))
        cosine = float(r.get("cosine_sim", 0.0) or 0.0)
        project_boost = _project_multiplier(
            str(r.get("project_id", "")), project_id, cross_project, backend
        )
        blacklisted = rid in rejected_ids
        privacy_blocked = (str(r.get("privacy", "")) == "confidential" and not include_confidential)
        kept = (not blacklisted) and (project_boost > 0.0) and (not privacy_blocked)
        score = cosine * project_boost if kept else 0.0
        candidates.append(
            {
                "id": rid,
                "content_preview": str(r.get("content", ""))[:200],
                "kind": str(r.get("kind", "")),
                "project_id": str(r.get("project_id", "")),
                "privacy": str(r.get("privacy", "")),
                "pinned": bool(r.get("pinned", 0)),
                "cosine_sim": cosine,
                "project_boost": project_boost,
                "blacklisted": blacklisted,
                "privacy_blocked": privacy_blocked,
                "score": score,
                "kept": kept,
            }
        )
        if kept:
            hits.append(
                Hit(
                    id=rid,
                    content=str(r.get("content", "")),
                    kind=str(r.get("kind", "free")),
                    project_id=str(r.get("project_id", "")),
                    privacy=str(r.get("privacy", "private")),
                    status=str(r.get("status", "active")),
                    pinned=bool(r.get("pinned", 0)),
                    cosine_sim=cosine,
                    project_boost=project_boost,
                    score=score,
                    source=str(r.get("source", "")),
                    tags=list(r.get("tags") or []),
                    updated_at=str(r.get("updated_at", "")),
                )
            )

    hits.sort(key=lambda h: (h.pinned, h.score), reverse=True)
    return {
        "query": query,
        "filters": {
            "project_id": project_id,
            "include_confidential": include_confidential,
            "cross_project": cross_project,
            "kind": kind,
        },
        "hits": [h.to_dict() for h in hits[: int(limit)]],
        "candidates": candidates,
    }
