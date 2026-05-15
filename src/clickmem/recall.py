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
from typing import Any, Iterable, List

from clickmem.backend import Backend, get_backend
from clickmem.blacklist import enforce_on_recall
from clickmem.embedding import embed
from clickmem.events import write as event_write
from clickmem.projects import allowed_cross_refs, get as get_project
from clickmem.sqlutil import quote_array_str, quote_str


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
    tag_boost: float = 1.0
    tag_match_count: int = 0
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
            "tag_boost": float(self.tag_boost),
            "tag_match_count": int(self.tag_match_count),
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


def _normalise_tags(tags: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        cleaned = str(tag).strip()
        if not cleaned or cleaned in seen:
            continue
        out.append(cleaned)
        seen.add(cleaned)
    return out


def _normalise_tag_mode(tag_mode: str | None) -> str:
    mode = (tag_mode or "any").strip().lower()
    return mode if mode in {"any", "all"} else "any"


def _tag_predicate(tags: list[str], tag_mode: str) -> str | None:
    if not tags:
        return None
    fn = "hasAll" if tag_mode == "all" else "hasAny"
    return f"{fn}(tags, {quote_array_str(tags)})"


def _tag_match_count(hit_tags: Iterable[str] | None, requested_tags: list[str]) -> int:
    if not requested_tags:
        return 0
    hit_set = {str(t) for t in (hit_tags or [])}
    return sum(1 for tag in requested_tags if tag in hit_set)


def _tag_boost(match_count: int, requested_tags: list[str]) -> float:
    if not requested_tags:
        return 1.0
    # Tags are already a filter; keep the ranking nudge intentionally small.
    return 1.0 + min(0.15, match_count * 0.05)


def _eligible_project_ids(project_id: str, cross_project: bool, backend: Backend) -> list[str] | None:
    """Return SQL-prefilter project ids, or None when all projects are eligible."""
    if cross_project:
        return None
    if not project_id:
        return [""]
    ids = [project_id, ""]
    project = get_project(project_id, backend=backend)
    if project:
        ids.extend(str(pid) for pid in (project.allowed_cross_refs or []) if str(pid).strip())
    seen: set[str] = set()
    out: list[str] = []
    for pid in ids:
        if pid in seen:
            continue
        out.append(pid)
        seen.add(pid)
    return out


def _project_predicate(project_id: str, cross_project: bool, backend: Backend) -> str | None:
    ids = _eligible_project_ids(project_id, cross_project, backend)
    if ids is None:
        return None
    if len(ids) == 1:
        return f"project_id = {quote_str(ids[0])}"
    return "project_id IN (" + ", ".join(quote_str(pid) for pid in ids) + ")"


def recall(
    query: str,
    *,
    project_id: str = "",
    limit: int = 10,
    include_confidential: bool = False,
    cross_project: bool = False,
    kind: str | None = None,
    tags: Iterable[str] | None = None,
    tag_mode: str = "any",
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
    requested_tags = _normalise_tags(tags)
    tag_mode = _normalise_tag_mode(tag_mode)
    tag_clause = _tag_predicate(requested_tags, tag_mode)
    if tag_clause:
        where_parts.append(tag_clause)
    project_clause = _project_predicate(project_id, cross_project, backend)
    if project_clause:
        where_parts.append(project_clause)
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
        hit_tags = list(r.get("tags") or [])
        tag_match_count = _tag_match_count(hit_tags, requested_tags)
        tag_boost = _tag_boost(tag_match_count, requested_tags)
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
                tag_boost=tag_boost,
                tag_match_count=tag_match_count,
                score=cosine * mult * tag_boost,
                source=str(r.get("source", "")),
                tags=hit_tags,
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
        payload={
            "query_preview": query[:120],
            "hit_ids": [h.id for h in out],
            "tags": requested_tags,
            "tag_mode": tag_mode,
        },
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
    tags: Iterable[str] | None = None,
    tag_mode: str = "any",
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
    requested_tags = _normalise_tags(tags)
    tag_mode = _normalise_tag_mode(tag_mode)
    tag_clause = _tag_predicate(requested_tags, tag_mode)
    if tag_clause:
        where_parts.append(tag_clause)
    project_clause = _project_predicate(project_id, cross_project, backend)
    if project_clause:
        where_parts.append(project_clause)
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
        hit_tags = list(r.get("tags") or [])
        tag_match_count = _tag_match_count(hit_tags, requested_tags)
        tag_boost = _tag_boost(tag_match_count, requested_tags)
        blacklisted = rid in rejected_ids
        privacy_blocked = (str(r.get("privacy", "")) == "confidential" and not include_confidential)
        kept = (not blacklisted) and (project_boost > 0.0) and (not privacy_blocked)
        score = cosine * project_boost * tag_boost if kept else 0.0
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
                "tag_boost": tag_boost,
                "tag_match_count": tag_match_count,
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
                    tag_boost=tag_boost,
                    tag_match_count=tag_match_count,
                    score=score,
                    source=str(r.get("source", "")),
                    tags=hit_tags,
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
            "tags": requested_tags,
            "tag_mode": tag_mode,
        },
        "hits": [h.to_dict() for h in hits[: int(limit)]],
        "candidates": candidates,
    }
