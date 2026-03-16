"""CEO Retrieval — cross-entity hybrid search for CEO Brain.

Replaces retrieval.py. Searches across decisions, principles, and episodes
with entity-type-specific scoring adjustments.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB


_SAME_PROJECT_BOOST = 1.3
_GLOBAL_BOOST = 1.0
_OTHER_PROJECT_PENALTY = 0.6


def ceo_search(
    ceo_db: CeoDB,
    emb,
    query: str,
    project_id: str | None = None,
    entity_types: list[str] | None = None,
    top_k: int = 10,
    domain: str | None = None,
    include_global: bool = True,
) -> list[dict]:
    """Unified search across CEO entities with project-aware scoring.

    When project_id is set, results are scope-boosted:
    - Same project: 1.3x (most relevant)
    - Global (project_id=""): 1.0x (universally applicable)
    - Other project: 0.6x (may be cross-project noise)

    Returns list of dicts with keys:
    entity_type, id, content, score, metadata
    """
    if not query or not query.strip():
        return []

    query_vec = emb.encode_query(query[:500])
    types = entity_types or ["decisions", "principles", "episodes"]
    results: list[dict] = []

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

    # MMR-style diversity: ensure we don't return too many of the same type
    return _mmr_diverse(unique, top_k)


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
