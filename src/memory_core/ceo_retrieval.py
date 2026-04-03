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
_KW_BONUS_WEIGHT = 0.6  # keyword bonus multiplier (was 0.3: too weak to override vector gaps)
_KW_BONUS_CAP = 2.0  # max keyword boost (was 1.5)
_RRF_K = 60  # reciprocal rank fusion constant

# Fallback regex tokenizer (used only when LLM is unavailable)
# Matches: dot-prefixed paths (.cursor-plugin), dunder names (__del__),
# alphanumeric tokens, and CJK characters
_WORD_RE = re.compile(r'\.[\w.\-]+|__\w+__|[a-zA-Z0-9_.\-@/]+|[\u4e00-\u9fff\u3400-\u4dbf]+')


def _tokenize_query_regex(query: str) -> list[str]:
    """Fallback: regex-based tokenization when LLM is unavailable."""
    tokens = _WORD_RE.findall(query)
    seen: set[str] = set()
    return [t for t in tokens if t not in seen and not seen.add(t)]


_KEYWORD_EXTRACT_PROMPT = """\
Extract search keywords from this query for a memory recall system.

Query: {query}

Return ONLY a JSON array of keywords (strings). Include:
- Key nouns and technical terms in the original language
- English translations of non-English terms
- Synonyms that someone might have used when storing this information

Example: query "我的 agent 开发原则" → ["agent", "开发", "原则", "development", "principle", "guideline", "rule"]

Return ONLY the JSON array, no other text.
"""


def _tokenize_query(query: str, llm_complete=None) -> list[str]:
    """Extract search keywords. Uses LLM when available, regex fallback."""
    if llm_complete is not None:
        try:
            import json
            raw = llm_complete(_KEYWORD_EXTRACT_PROMPT.format(query=query))
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(l for l in lines if not l.strip().startswith("```")).strip()
            keywords = json.loads(text)
            if isinstance(keywords, list) and keywords:
                return [str(k) for k in keywords if k]
        except Exception:
            pass
    return _tokenize_query_regex(query)


def _keyword_score(content: str, keywords: list[str],
                    entities: list[str] | None = None) -> float:
    """Fraction of query keywords found in content + entities (case-insensitive).

    Also checks the entities list (IPs, paths, usernames) for exact matches,
    which is critical for infrastructure lookups.
    """
    if not keywords:
        return 0.0
    searchable = content.lower()
    if entities:
        searchable += " " + " ".join(entities).lower()
    hits = sum(1 for kw in keywords if kw.lower() in searchable)
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
    llm_complete: Callable[[str], str] | None = None,
    use_query_expansion: bool = False,
    use_llm_rerank: bool = False,
) -> list[dict]:
    """Unified search across CEO entities with project-aware scoring.

    When project_id is set, results are scope-boosted:
    - Same project: 1.3x (most relevant)
    - Global (project_id=""): 1.0x (universally applicable)
    - Other project: 0.6x (may be cross-project noise)

    When session_id is set, session topic tracking is used for scope matching.
    When task_context is set, it provides explicit task context for scope matching.

    LLM-enhanced modes (require llm_complete):
    - use_query_expansion: LLM generates keyword expansions and sub-queries
    - use_llm_rerank: LLM reranks top candidates after retrieval

    Returns list of dicts with keys:
    entity_type, id, content, score, metadata
    """
    if not query or not query.strip():
        return []

    t0 = time.monotonic()

    # Stage 1: Query analysis
    from memory_core.ceo_query_analyzer import analyze_query_fast, analyze_query_llm
    if use_query_expansion and llm_complete:
        qa = analyze_query_llm(query, llm_complete)
    else:
        qa = analyze_query_fast(query)

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
                score = (1.0 - dist) * (0.7 + 0.3 * p.confidence)
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
                        "entities": ft.entities or [],
                    },
                })

    # ------------------------------------------------------------------
    # Keyword search: LLM extracts keywords (cross-language), regex fallback
    # ------------------------------------------------------------------
    keywords = _tokenize_query(query, llm_complete=llm_complete)
    # Merge with any expanded terms from query analysis
    for t in qa.expanded_terms:
        if t not in keywords:
            keywords.append(t)
    # Map entity_type names to the types list format (e.g. "decision" → "decisions")
    _TYPE_TO_PLURAL = {"decision": "decisions", "principle": "principles",
                       "episode": "episodes", "fact": "facts"}
    kw_results: list[dict] = []
    if keywords:
        try:
            raw_kw = ceo_db.search_by_keywords(keywords, project_id=None, limit=top_k * 2)
            # Filter to only entity types the caller requested
            kw_results = [r for r in raw_kw
                          if _TYPE_TO_PLURAL.get(r.get("entity_type", ""), "") in types]
        except Exception as exc:
            logger.debug("Keyword search failed: %s", exc)

    # ------------------------------------------------------------------
    # Sub-query multi-vector search (when query has multiple intents)
    # ------------------------------------------------------------------
    if len(qa.sub_queries) > 1:
        for sq in qa.sub_queries:
            sq_vec = emb.encode_query(sq[:500])
            for tbl_type in types:
                try:
                    if tbl_type == "decisions":
                        items = ceo_db.search_decisions_by_vector(sq_vec, project_id=None, limit=5)
                        for d in items:
                            dist = ceo_db._cosine_dist(sq_vec, d.embedding) if d.embedding else 1.0
                            results.append({
                                "entity_type": "decision", "id": d.id,
                                "content": f"{d.title}: {d.choice}",
                                "score": (1.0 - dist) * _project_boost(d.project_id),
                                "metadata": {"domain": d.domain, "project_id": d.project_id,
                                              "outcome_status": d.outcome_status},
                            })
                    elif tbl_type == "facts":
                        items = ceo_db.search_facts_by_vector(sq_vec, project_id=None, limit=5)
                        for ft in items:
                            dist = ceo_db._cosine_dist(sq_vec, ft.embedding) if ft.embedding else 1.0
                            results.append({
                                "entity_type": "fact", "id": ft.id,
                                "content": ft.content,
                                "score": (1.0 - dist) * _project_boost(ft.project_id),
                                "metadata": {"category": ft.category, "domain": ft.domain,
                                              "project_id": ft.project_id},
                            })
                except Exception:
                    pass

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

    # Named entities from query (IPs, paths, user@host) for exact-match boosting
    query_entities_lower = {e.lower() for e in qa.named_entities}

    # Compute final score: vector_score * keyword_boost, with RRF bonus for keyword hits
    for rid, r in by_id.items():
        base_score = r.get("score", 0.0)

        # Keyword boost: fraction of keywords found in content + entities
        meta = r.get("metadata", {}) or {}
        entities = meta.get("entities") or meta.get("tags") or []
        content_text = r.get("content", "")
        kw_frac = _keyword_score(content_text, keywords, entities=entities) if keywords else 0.0
        # Length normalization: long content naturally matches more keywords,
        # penalize to prevent decisions from dominating over shorter facts/principles.
        # Only penalize the reasoning portion — title+choice are fair game.
        reasoning_len = len(meta.get("reasoning", ""))
        if reasoning_len > 100:
            kw_frac *= min(1.0, 100.0 / reasoning_len)
        kw_boost = min(_KW_BONUS_CAP, 1.0 + _KW_BONUS_WEIGHT * kw_frac)

        # Entity exact-match boost: when query has named entities (IP, path, user@host)
        # and a result's entities list contains them, give a strong boost
        if query_entities_lower and entities:
            result_entities_lower = {e.lower() for e in entities}
            entity_hits = query_entities_lower & result_entities_lower
            if entity_hits:
                kw_boost *= 1.5  # strong boost for exact entity match

        # Fact specificity boost: facts with keyword relevance are precise matches
        if r.get("entity_type") == "fact" and kw_frac > 0.3:
            kw_boost *= 1.3

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

    # Optional LLM reranking
    candidates = unique[:top_k * 2]  # feed more candidates to LLM
    if use_llm_rerank and llm_complete and len(candidates) > 1:
        candidates = _llm_rerank(query, candidates, llm_complete, top_k)

    # JSONL recall logging — always-on for traceability
    from memory_core.recall_logger import log_recall
    final = _mmr_diverse(candidates, top_k)
    log_recall(
        query=query, project_id=project_id or "",
        session_id=session_id or "", results=final,
        latency_ms=elapsed_ms,
        keywords=keywords,
        expanded_terms=qa.expanded_terms,
        named_entities=qa.named_entities,
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


_LLM_RERANK_PROMPT = """\
Given the user's query, rank these memory results by relevance.
Return ONLY a JSON array of result numbers in order of relevance (most relevant first).

Query: {query}

Results:
{results_text}

Return ONLY a JSON array like [3, 1, 5, 2, 4] — no other text.
"""


def _llm_rerank(
    query: str,
    candidates: list[dict],
    llm_complete: Callable[[str], str],
    top_k: int = 10,
) -> list[dict]:
    """Ask LLM to rerank candidates by relevance to query.

    Falls back to original ordering on any failure.
    """
    if len(candidates) <= 1:
        return candidates

    # Build numbered list for LLM
    lines = []
    for i, r in enumerate(candidates[:15], 1):  # cap at 15 to fit context
        etype = r.get("entity_type", "")
        content = r.get("content", "").replace("\n", " ")[:150]
        lines.append(f"{i}. [{etype}] {content}")

    prompt = _LLM_RERANK_PROMPT.format(
        query=query,
        results_text="\n".join(lines),
    )

    try:
        import json
        raw = llm_complete(prompt)
        text = raw.strip()
        # Strip markdown fences
        if text.startswith("```"):
            text_lines = text.split("\n")
            text_lines = [ln for ln in text_lines if not ln.strip().startswith("```")]
            text = "\n".join(text_lines).strip()

        rankings = json.loads(text)
        if not isinstance(rankings, list):
            return candidates[:top_k]

        # Reorder candidates by LLM ranking
        reranked: list[dict] = []
        seen: set[int] = set()
        for idx in rankings:
            if isinstance(idx, int) and 1 <= idx <= len(candidates) and idx not in seen:
                reranked.append(candidates[idx - 1])
                seen.add(idx)

        # Append any candidates the LLM missed
        for i, r in enumerate(candidates):
            if (i + 1) not in seen:
                reranked.append(r)

        return reranked[:top_k]

    except Exception as exc:
        logger.debug("LLM rerank failed: %s", exc)
        return candidates[:top_k]
