"""CEO Retrieval — cross-entity hybrid search for CEO Brain.

Replaces retrieval.py. Searches across decisions, principles, episodes, and facts
with entity-type-specific scoring, keyword matching, and session-aware scope matching.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB

logger = logging.getLogger(__name__)


_SAME_PROJECT_BOOST = 1.3
_GLOBAL_BOOST = 1.0
_OTHER_PROJECT_PENALTY = 0.6

_SCOPE_MATCH_BOOST = 1.2
_SCOPE_MISMATCH_PENALTY = 0.3

# Keyword matching constants
_KW_BONUS_WEIGHT = 0.3  # keyword bonus multiplier
_KW_BONUS_CAP = 1.5  # max keyword boost
_RRF_K = 60  # reciprocal rank fusion constant

# CJK Unicode ranges for tokenization
_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]+')
_WORD_RE = re.compile(r'[a-zA-Z0-9_.\-@/]+|[\u4e00-\u9fff\u3400-\u4dbf]')

# Common stopwords (bilingual, small set)
_STOPWORDS = frozenset({
    "的", "是", "在", "了", "我", "我的", "你", "他", "她", "它", "们",
    "这", "那", "什么", "怎么", "哪里", "哪个", "用", "和", "与",
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at",
    "to", "for", "of", "and", "or", "my", "your", "his", "her", "its",
    "what", "which", "where", "how", "who", "do", "does", "did",
})


def _tokenize_query(query: str) -> list[str]:
    """Extract searchable keywords from a query string.

    Handles CJK characters (split into individual chars) and
    Latin words. Filters stopwords and short tokens.
    """
    tokens = _WORD_RE.findall(query)
    keywords = []
    for t in tokens:
        t_lower = t.lower()
        if t_lower in _STOPWORDS:
            continue
        if len(t) == 1 and not _CJK_RE.match(t):
            continue  # skip single ASCII chars
        keywords.append(t)
    return keywords


def _keyword_score(content: str, keywords: list[str]) -> float:
    """Fraction of query keywords found in content (case-insensitive)."""
    if not keywords:
        return 0.0
    content_lower = content.lower()
    hits = sum(1 for kw in keywords if kw.lower() in content_lower)
    return hits / len(keywords)


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
    types = entity_types or ["decisions", "principles", "episodes", "facts"]
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

        if "facts" in types:
            facts = ceo_db.search_facts_by_vector(query_vec, project_id=pid, limit=top_k)
            for ft in facts:
                if domain and ft.domain != domain:
                    continue
                dist = ceo_db._cosine_dist(query_vec, ft.embedding) if ft.embedding else 1.0
                score = 1.0 - dist
                # No time decay for facts — they stay valid until explicitly updated
                score *= _project_boost(ft.project_id)
                results.append({
                    "entity_type": "fact",
                    "id": ft.id,
                    "content": ft.content,
                    "score": score,
                    "metadata": {
                        "category": ft.category,
                        "domain": ft.domain,
                        "project_id": ft.project_id,
                    },
                })

    # ------------------------------------------------------------------
    # Keyword search: find records matching query keywords
    # ------------------------------------------------------------------
    keywords = _tokenize_query(query)
    kw_results: list[dict] = []
    if keywords:
        try:
            kw_results = ceo_db.search_by_keywords(keywords, project_id=None, limit=top_k * 2)
        except Exception as exc:
            logger.debug("Keyword search failed: %s", exc)

    # ------------------------------------------------------------------
    # Fusion: merge vector results + keyword results via RRF + keyword boost
    # ------------------------------------------------------------------
    # Build rank maps for RRF
    vec_rank: dict[str, int] = {}
    for i, r in enumerate(results):
        if r["id"] not in vec_rank:
            vec_rank[r["id"]] = i

    kw_rank: dict[str, int] = {}
    for i, r in enumerate(kw_results):
        if r["id"] not in kw_rank:
            kw_rank[r["id"]] = i

    # Merge all results by id
    by_id: dict[str, dict] = {}
    for r in results:
        by_id.setdefault(r["id"], r)
    for r in kw_results:
        by_id.setdefault(r["id"], r)

    # Compute final score: vector_score * keyword_boost, with RRF bonus for keyword hits
    for rid, r in by_id.items():
        base_score = r.get("score", 0.0)

        # Keyword boost: fraction of keywords found in content
        kw_frac = _keyword_score(r.get("content", ""), keywords) if keywords else 0.0
        kw_boost = min(_KW_BONUS_CAP, 1.0 + _KW_BONUS_WEIGHT * kw_frac)

        # RRF bonus: reward items found by both strategies
        rrf = 0.0
        if rid in vec_rank:
            rrf += 1.0 / (_RRF_K + vec_rank[rid])
        if rid in kw_rank:
            rrf += 1.0 / (_RRF_K + kw_rank[rid])

        # Items only from keyword search (no vector score) get a base from RRF
        if base_score == 0 and rrf > 0:
            base_score = rrf * 50  # scale RRF to ~0.5-0.8 range

        r["score"] = base_score * kw_boost + rrf * 0.1

    unique = list(by_id.values())

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
