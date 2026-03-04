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


def _keyword_score(content: str, tags: list[str], query_words: list[str]) -> float:
    """Simple keyword matching score. Returns value in [0, 1]."""
    if not query_words:
        return 0.0
    content_lower = content.lower()
    tag_set = {t.lower() for t in tags}
    hits = 0
    for w in query_words:
        wl = w.lower()
        if wl in content_lower or wl in tag_set:
            hits += 1
    return hits / len(query_words)


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

    # Determine which layers to search
    if cfg.layer:
        layers = [cfg.layer]
    else:
        layers = ["episodic", "semantic"]

    # Fetch candidates from all applicable layers
    candidates: list[dict] = []
    for layer in layers:
        memories = db.list_by_layer(layer, limit=1000)
        for m in memories:
            if cfg.category and m.category != cfg.category:
                continue
            candidates.append({
                "id": m.id,
                "layer": m.layer,
                "category": m.category,
                "content": m.content,
                "tags": m.tags,
                "embedding": m.embedding,
                "created_at": m.created_at,
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

        # Keyword score
        kw_score = _keyword_score(c["content"], c.get("tags", []), query_words)

        # Base score: weighted combination
        base_score = cfg.w_vector * vec_score + cfg.w_keyword * kw_score

        # Time decay for episodic memories
        if c["layer"] == "episodic":
            decay = _time_decay(c["created_at"], cfg.decay_days)
            base_score *= decay

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
) -> list[dict]:
    """Apply Maximal Marginal Relevance to select diverse results."""
    if not candidates:
        return []

    selected: list[dict] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_idx = -1
        best_mmr = -float("inf")

        for i, cand in enumerate(remaining):
            relevance = cand["_base_score"]

            # Max similarity to already selected items
            max_sim = 0.0
            if selected and cand.get("embedding"):
                for s in selected:
                    if s.get("embedding"):
                        sim = _cosine_similarity(cand["embedding"], s["embedding"])
                        max_sim = max(max_sim, sim)

            mmr_score = mmr_lambda * relevance - (1.0 - mmr_lambda) * max_sim
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        if best_idx < 0:
            break

        winner = remaining.pop(best_idx)
        winner["_final_score"] = winner["_base_score"]  # final score is base score
        selected.append(winner)

    # Re-sort selected by final_score descending
    selected.sort(key=lambda c: c["_final_score"], reverse=True)
    return selected
