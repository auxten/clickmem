"""Hybrid search retrieval with vector+keyword scoring, time decay, and MMR."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from memory_core.models import RetrievalConfig

if TYPE_CHECKING:
    from memory_core.db import MemoryDB


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-10 or nb < 1e-10:
        return 0.0
    return dot / (na * nb)


def _time_decay(created_at: datetime | None, half_life_days: float) -> float:
    """Exponential decay factor based on age. Returns value in (0, 1]."""
    if created_at is None:
        return 1.0
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_days = max(0, (now - created_at).total_seconds() / 86400)
    if half_life_days <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days / half_life_days)


def _keyword_score(content: str, tags: list[str], query_words: list[str],
                   entities: list[str] | None = None) -> float:
    """Keyword matching score over content, tags, and entities. Returns [0, 1]."""
    if not query_words:
        return 0.0
    content_lower = content.lower()
    tag_set = {t.lower() for t in tags}
    entity_set = {e.lower() for e in (entities or [])}
    hits = 0
    for w in query_words:
        wl = w.lower()
        if wl in content_lower or wl in tag_set or wl in entity_set:
            hits += 1
    return hits / len(query_words)


def _popularity_boost(access_count: int) -> float:
    """Logarithmic popularity boost from access frequency. Returns [1.0, ~1.15].

    Frequently recalled memories get a mild score boost:
      0 accesses → 1.00
      5 accesses → 1.05
      50 accesses → 1.10
      500 accesses → 1.15
    """
    if access_count <= 0:
        return 1.0
    return 1.0 + 0.03 * math.log1p(access_count)


def recency_score(
    created_at: datetime | None,
    tau: float = 60.0,
    k: float = 0.15,
) -> float:
    """Compute a recency score in [0.8, 1.0] using logarithmic time decay.

    Uses ``1 / (1 + k * ln(1 + age/tau))`` which has **high near-term
    sensitivity** and **low far-term sensitivity** — matching how humans
    perceive time differences (Weber-Fechner law):

    - 1 min vs 2 min → noticeable difference (~0.009)
    - 3 months vs 4 months → tiny difference (~0.001)
    - Near/far sensitivity ratio ≈ 17×

    The raw [0, 1] score is mapped to [0.8, 1.0] so recency is a mild
    tiebreaker rather than the dominant factor.

    Parameters
    ----------
    created_at : datetime or None
        When the memory was created/updated.  None → returns 1.0 (max).
    tau : float
        Time granularity anchor in seconds.  Default 60 (= 1 minute).
        Smaller → more sensitive to very short intervals.
    k : float
        Decay steepness.  Default 0.15 (gentle).
    """
    if created_at is None:
        return 1.0
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age_seconds = max(0.0, (now - created_at).total_seconds())
    raw = 1.0 / (1.0 + k * math.log1p(age_seconds / tau))
    return 0.8 + 0.2 * raw


_NOISE_PATTERNS = [
    re.compile(r"\[cron:[0-9a-f-]+"),
    re.compile(r"A new session was started via /new"),
    re.compile(r"<clickmem-context>"),
    re.compile(r"Execute your Session Startup sequence"),
    re.compile(r"Current time: .+\(Asia/"),
]
_NOISE_PENALTY = 0.3


def _noise_penalty(content: str) -> float:
    """Return a score multiplier < 1 for system/operational noise content."""
    for pat in _NOISE_PATTERNS:
        if pat.search(content):
            return _NOISE_PENALTY
    return 1.0


_RECENCY_HINT_RE = re.compile(
    r"recently|recent|latest|last\s+(?:few\s+)?(?:day|week|month)|"
    r"最近|近期|上周|这周|今天|昨天|过去几天",
    re.IGNORECASE,
)
_RECENCY_DECAY_DAYS = 7.0


def _detect_recency_hint(query: str) -> float | None:
    """Return a shorter decay half-life if the query implies recency."""
    if _RECENCY_HINT_RE.search(query):
        return _RECENCY_DECAY_DAYS
    return None


def _tokenize_query(query: str) -> list[str]:
    """Split query into searchable words."""
    return [w for w in re.split(r"\s+", query.strip()) if w]


def hybrid_search(
    db: "MemoryDB",
    emb,
    query: str,
    cfg: RetrievalConfig | None = None,
) -> list[dict]:
    """Run hybrid vector+keyword search over memories.

    Retrieves candidates from db, scores with vector similarity + keyword matching,
    applies time decay for episodic, then applies MMR for diversity.
    """
    if cfg is None:
        cfg = RetrievalConfig()

    query_words = _tokenize_query(query)
    query_vec = emb.encode_query(query) if query.strip() else None

    recency_decay = _detect_recency_hint(query)
    if recency_decay is not None:
        cfg = RetrievalConfig(
            top_k=cfg.top_k, w_vector=cfg.w_vector, w_keyword=cfg.w_keyword,
            decay_days=recency_decay, mmr_lambda=cfg.mmr_lambda,
            semantic_boost=cfg.semantic_boost,
            layer=cfg.layer, category=cfg.category,
        )

    # Determine which layers to search
    if cfg.layer:
        layers = [cfg.layer]
    else:
        layers = ["episodic", "semantic"]

    # Fetch candidates — use SQL-level vector pre-filter when possible
    candidates: list[dict] = []
    for layer in layers:
        if query_vec:
            memories = db.search_by_vector(query_vec, layer, limit=200)
        else:
            memories = db.list_by_layer(layer, limit=200)
        for m in memories:
            if cfg.category and m.category != cfg.category:
                continue
            candidates.append({
                "id": m.id,
                "layer": m.layer,
                "category": m.category,
                "content": m.content,
                "tags": m.tags,
                "entities": m.entities,
                "embedding": m.embedding,
                "created_at": m.created_at,
                "access_count": m.access_count,
            })

    if not candidates:
        return []

    # Score each candidate
    for c in candidates:
        # Vector score
        vec_score = 0.0
        if query_vec and c.get("embedding"):
            sim = _cosine_similarity(query_vec, c["embedding"])
            vec_score = max(0.0, (sim + 1.0) / 2.0)  # map [-1,1] to [0,1]

        # Keyword score (includes entities)
        kw_score = _keyword_score(c["content"], c.get("tags", []), query_words,
                                  c.get("entities"))

        # Base score: weighted combination
        base_score = cfg.w_vector * vec_score + cfg.w_keyword * kw_score

        # Layer-specific modifiers
        if c["layer"] == "episodic":
            base_score *= _time_decay(c["created_at"], cfg.decay_days)
            base_score *= _noise_penalty(c["content"])
        elif c["layer"] == "semantic":
            base_score *= recency_score(c["created_at"])
            base_score *= cfg.semantic_boost

        # Popularity boost from access frequency
        base_score *= _popularity_boost(c.get("access_count", 0))

        c["_vec_score"] = vec_score
        c["_base_score"] = base_score

    # Sort by base score for MMR candidate selection
    candidates.sort(key=lambda c: c["_base_score"], reverse=True)

    # Apply MMR
    selected = _apply_mmr(candidates, cfg.mmr_lambda, cfg.top_k)

    # Build result dicts
    results = []
    for c in selected:
        results.append({
            "id": c["id"],
            "layer": c["layer"],
            "category": c["category"],
            "content": c["content"],
            "final_score": c["_final_score"],
            "tags": c.get("tags", []),
        })

    return results


def _apply_mmr(
    candidates: list[dict],
    mmr_lambda: float,
    top_k: int,
    dedup_threshold: float = 0.92,
) -> list[dict]:
    """Apply MMR with aggressive near-duplicate suppression.

    Any candidate whose embedding cosine similarity to an already-selected
    item exceeds ``dedup_threshold`` is silently dropped (not just penalized).
    This prevents N copies of the same cron-job / repeated content from
    monopolizing the result set.
    """
    if not candidates:
        return []

    selected: list[dict] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_idx = -1
        best_mmr = -float("inf")

        for i, cand in enumerate(remaining):
            relevance = cand["_base_score"]

            max_sim = 0.0
            is_duplicate = False
            if selected and cand.get("embedding"):
                for s in selected:
                    if s.get("embedding"):
                        sim = _cosine_similarity(cand["embedding"], s["embedding"])
                        if sim > dedup_threshold:
                            is_duplicate = True
                            break
                        max_sim = max(max_sim, sim)

            if is_duplicate:
                continue

            mmr_score = mmr_lambda * relevance - (1.0 - mmr_lambda) * max_sim
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        if best_idx < 0:
            # All remaining candidates are duplicates — done
            break

        winner = remaining.pop(best_idx)
        winner["_final_score"] = winner["_base_score"]
        selected.append(winner)

    selected.sort(key=lambda c: c["_final_score"], reverse=True)
    return selected
