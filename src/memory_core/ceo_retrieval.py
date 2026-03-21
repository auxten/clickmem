"""CEO Retrieval — cross-entity hybrid search for CEO Brain.

Replaces retrieval.py. Searches across decisions, principles, and episodes
with entity-type-specific scoring adjustments and session-aware scope matching.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB

logger = logging.getLogger(__name__)


_SAME_PROJECT_BOOST = 1.3
_GLOBAL_BOOST = 1.0
_OTHER_PROJECT_PENALTY = 0.6

_SCOPE_MATCH_BOOST = 1.2
_SCOPE_MISMATCH_PENALTY = 0.3


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _scope_score(
    scope_embedding: list[float] | None,
    query_vec: list[float],
    session_topic_vec: list[float] | None,
    task_context_vec: list[float] | None,
) -> float:
    """Returns multiplier: 1.2 (match), 0.3 (mismatch), 1.0 (no scope)."""
    if not scope_embedding:
        return 1.0  # no scope = global

    # Pick context vector: task_context > session_topic > query-only
    context_vec = task_context_vec or session_topic_vec

    if context_vec is not None:
        # Fuse context (0.6) + query (0.4)
        context_sim = _cosine_sim(context_vec, scope_embedding)
        query_sim = _cosine_sim(query_vec, scope_embedding)
        fused = 0.6 * context_sim + 0.4 * query_sim
    else:
        fused = _cosine_sim(query_vec, scope_embedding)

    if fused > 0.5:
        return _SCOPE_MATCH_BOOST
    elif fused < 0.3:
        return _SCOPE_MISMATCH_PENALTY
    else:
        return 1.0


def ceo_search(
    ceo_db: CeoDB,
    emb,
    query: str,
    project_id: str | None = None,
    entity_types: list[str] | None = None,
    top_k: int = 10,
    domain: str | None = None,
    include_global: bool = True,
    session_id: str | None = None,
    task_context: str | None = None,
) -> list[dict]:
    """Unified search across CEO entities with project-aware scoring.

    When project_id is set, results are scope-boosted:
    - Same project: 1.3x (most relevant)
    - Global (project_id=""): 1.0x (universally applicable)
    - Other project: 0.6x (may be cross-project noise)

    When session_id is set, session topic tracking is used for scope matching.
    When task_context is set, it provides explicit task context for scope matching.

    Returns list of dicts with keys:
    entity_type, id, content, score, metadata
    """
    if not query or not query.strip():
        return []

    t0 = time.monotonic()
    query_vec = emb.encode_query(query[:500])
    types = entity_types or ["decisions", "principles", "episodes"]
    results: list[dict] = []

    # Session topic tracking
    session_topic_vec = None
    task_context_vec = None

    if session_id:
        from memory_core.session_context import get_session_store
        store = get_session_store()
        store.update(session_id, query_vec, query)
        session_topic_vec = store.get_topic_embedding(session_id)

    if task_context:
        task_context_vec = emb.encode_query(task_context[:500])

    # Always search everything, then apply project-aware score boosting
    search_pids: list[str | None] = [None]

    def _project_boost(item_project_id: str) -> float:
        """Score multiplier based on project scope relevance."""
        if not project_id:
            return 1.0
        if item_project_id == project_id:
            return _SAME_PROJECT_BOOST
        if not item_project_id:
            return _GLOBAL_BOOST
        return _OTHER_PROJECT_PENALTY

    for pid in search_pids:
        if "decisions" in types:
            decisions = ceo_db.search_decisions_by_vector(query_vec, project_id=pid, limit=top_k)
            for d in decisions:
                if domain and d.domain != domain:
                    continue
                dist = ceo_db._cosine_dist(query_vec, d.embedding) if d.embedding else 1.0
                score = 1.0 - dist
                if d.outcome_status == "validated":
                    score *= 1.2
                score *= _project_boost(d.project_id)
                # Apply scope scoring for decisions
                scope_mult = _scope_score(d.scope_embedding, query_vec, session_topic_vec, task_context_vec)
                score *= scope_mult
                results.append({
                    "entity_type": "decision",
                    "id": d.id,
                    "content": f"{d.title}: {d.choice}",
                    "score": score,
                    "metadata": {
                        "reasoning": d.reasoning,
                        "domain": d.domain,
                        "outcome_status": d.outcome_status,
                        "project_id": d.project_id,
                        "scope_match": scope_mult,
                        "activation_scope": d.activation_scope,
                    },
                })

        if "principles" in types:
            principles = ceo_db.search_principles_by_vector(query_vec, project_id=pid, limit=top_k)
            for p in principles:
                if domain and p.domain != domain:
                    continue
                dist = ceo_db._cosine_dist(query_vec, p.embedding) if p.embedding else 1.0
                score = (1.0 - dist) * (0.5 + 0.5 * p.confidence)
                score *= _project_boost(p.project_id)
                # Apply scope scoring for principles
                scope_mult = _scope_score(p.scope_embedding, query_vec, session_topic_vec, task_context_vec)
                score *= scope_mult
                results.append({
                    "entity_type": "principle",
                    "id": p.id,
                    "content": p.content,
                    "score": score,
                    "metadata": {
                        "confidence": p.confidence,
                        "evidence_count": p.evidence_count,
                        "domain": p.domain,
                        "project_id": p.project_id,
                        "scope_match": scope_mult,
                        "activation_scope": p.activation_scope,
                    },
                })

        if "episodes" in types:
            episodes = ceo_db.search_episodes_by_vector(query_vec, project_id=pid, limit=top_k)
            for e in episodes:
                if domain and e.domain != domain:
                    continue
                dist = ceo_db._cosine_dist(query_vec, e.embedding) if e.embedding else 1.0
                score = 1.0 - dist
                if e.created_at:
                    age_days = (datetime.now(timezone.utc) - e.created_at).total_seconds() / 86400
                    decay = math.exp(-0.693 * age_days / 60.0)
                    score *= decay
                score *= _project_boost(e.project_id)
                # No scope scoring for episodes (factual records, not prescriptive)
                results.append({
                    "entity_type": "episode",
                    "id": e.id,
                    "content": e.content[:200],
                    "score": score,
                    "metadata": {
                        "user_intent": e.user_intent,
                        "domain": e.domain,
                        "project_id": e.project_id,
                    },
                })

    # Deduplicate by id (global + project may overlap)
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)

    # Sort by score desc
    unique.sort(key=lambda x: x["score"], reverse=True)

    elapsed_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "ceo_search query=%r project=%s results=%d top=%.3f ms=%.0f",
        query[:60], project_id or "*", len(unique),
        unique[0]["score"] if unique else 0.0, elapsed_ms,
    )

    # Optional JSONL recall logging
    from memory_core.recall_logger import log_recall
    final = _mmr_diverse(unique, top_k)
    log_recall(
        query=query, project_id=project_id or "",
        session_id=session_id or "", results=final,
        latency_ms=elapsed_ms,
    )

    # MMR-style diversity: ensure we don't return too many of the same type
    return final


def _mmr_diverse(results: list[dict], top_k: int, type_limit: int = 0) -> list[dict]:
    """Simple diversity filter: take top results, capping per entity type."""
    if not type_limit:
        return results[:top_k]

    type_counts: dict[str, int] = {}
    selected: list[dict] = []
    for r in results:
        etype = r["entity_type"]
        if type_counts.get(etype, 0) >= type_limit:
            continue
        selected.append(r)
        type_counts[etype] = type_counts.get(etype, 0) + 1
        if len(selected) >= top_k:
            break
    return selected
