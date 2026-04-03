"""Continual Refinement — deduplicate, merge, and refine L2 semantic memories.

Uses the local Qwen model with simple, pipeline-style prompts optimized
for small (2B parameter) models.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from memory_core.json_utils import extract_json_or
from memory_core.models import Memory

if TYPE_CHECKING:
    from memory_core.db import MemoryDB

_log = logging.getLogger("clickmem.refinement")

CLUSTER_SIMILARITY_THRESHOLD = 0.70

_DEDUP_PROMPT = """\
Are these two memories saying the same thing?

Memory A: {mem_a}
Memory B: {mem_b}

Return ONLY a JSON object:
{{"is_duplicate": true, "reason": "both describe the same preference"}}

If they are different:
{{"is_duplicate": false, "reason": "A is about X, B is about Y"}}
"""

_MERGE_PROMPT = """\
Merge these duplicate memories into one concise statement.

Memories:
{memories}

Return ONLY a JSON object:
{{"merged": "single concise statement capturing all information", "category": "knowledge", "tags": ["tag1"]}}
"""

_INCLUSION_PROMPT = """\
Should this memory be kept as long-term knowledge?

Memory: {content}

Criteria — keep only if ALL are true:
1. Actionable in future sessions
2. Stable (not likely to change soon)
3. Non-sensitive (no secrets, tokens, or personal data)

Return ONLY a JSON object:
{{"keep": true, "reason": "stable architecture decision"}}

If it should be removed:
{{"keep": false, "reason": "one-off task instruction"}}
"""


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-10 or nb < 1e-10:
        return 0.0
    return dot / (na * nb)


class ContinualRefinement:
    """Deduplicate, merge, and quality-gate L2 semantic memories."""

    @staticmethod
    def run(
        db: "MemoryDB",
        emb,
        llm_complete: Callable[[str], str],
    ) -> dict:
        _log.info("Starting continual refinement")
        exact_deduped = ContinualRefinement._dedup_exact_text(db)
        reextracted = ContinualRefinement._reextract_unprocessed(db, emb, llm_complete)
        clusters = ContinualRefinement._cluster_semantic(db, emb)
        merged = ContinualRefinement._refine_clusters(db, emb, llm_complete, clusters)
        pruned = ContinualRefinement._prune_low_quality(db, llm_complete)
        result = {
            "exact_deduped": exact_deduped,
            "reextracted": reextracted,
            "clusters_found": len(clusters),
            "merged": merged,
            "pruned": pruned,
        }
        _log.info("Refinement complete: %s", result)
        return result

    @staticmethod
    def _dedup_exact_text(db: "MemoryDB") -> int:
        """Remove exact-text duplicates in the semantic layer.

        Keeps the oldest memory (by created_at) and deactivates newer copies.
        """
        memories = db.list_by_layer("semantic", limit=500)
        content_groups: dict[str, list[Memory]] = {}
        for m in memories:
            key = m.content.strip().lower()
            content_groups.setdefault(key, []).append(m)
        deduped = 0
        for group in content_groups.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda m: m.created_at or datetime.min)
            for dup in group[1:]:
                db.deactivate(dup.id)
                deduped += 1
        return deduped

    @staticmethod
    def _reextract_unprocessed(
        db: "MemoryDB", emb, llm_complete: Callable[[str], str],
    ) -> int:
        """Re-extract memories from unprocessed raw transcripts."""
        unprocessed = db.list_unprocessed_raw(limit=50)
        if not unprocessed:
            return 0

        from memory_core.extractor import MemoryExtractor
        extractor = MemoryExtractor(db, emb)
        count = 0
        for raw in unprocessed:
            raw_id = raw["id"]
            content = raw.get("content", "")
            if not content or len(content) < 40:
                db.mark_raw_processed(raw_id)
                continue
            if "[object Object]" in content:
                db.mark_raw_processed(raw_id)
                continue
            try:
                ids = extractor.extract(
                    [{"role": "user", "content": content}],
                    llm_complete,
                    session_id=raw.get("session_id", ""),
                    raw_id=raw_id,
                )
                count += len(ids)
                if ids:
                    db.mark_raw_processed(raw_id)
            except Exception as exc:
                _log.warning("Re-extract failed for raw %s: %s", raw_id[:8], exc)
        return count

    @staticmethod
    def _cluster_semantic(db: "MemoryDB", emb) -> list[list[Memory]]:
        """Group similar L2 semantic memories into clusters by embedding cosine."""
        memories = db.list_by_layer("semantic", limit=500)
        if len(memories) < 2:
            return []

        assigned = set()
        clusters: list[list[Memory]] = []

        for i, mem_a in enumerate(memories):
            if mem_a.id in assigned:
                continue
            cluster = [mem_a]
            assigned.add(mem_a.id)
            for j in range(i + 1, len(memories)):
                mem_b = memories[j]
                if mem_b.id in assigned:
                    continue
                if mem_a.embedding and mem_b.embedding:
                    sim = _cosine_similarity(mem_a.embedding, mem_b.embedding)
                    if sim >= CLUSTER_SIMILARITY_THRESHOLD:
                        cluster.append(mem_b)
                        assigned.add(mem_b.id)
            if len(cluster) >= 2:
                clusters.append(cluster)

        return clusters

    _MAX_PAIRWISE_PER_CLUSTER = 10

    @staticmethod
    def _refine_clusters(
        db: "MemoryDB",
        emb,
        llm_complete: Callable[[str], str],
        clusters: list[list[Memory]],
    ) -> int:
        """For each cluster, check for duplicates and merge them."""
        merged_count = 0

        for cluster in clusters:
            duplicates = []
            pair_count = 0

            for i in range(len(cluster)):
                for j in range(i + 1, len(cluster)):
                    if pair_count >= ContinualRefinement._MAX_PAIRWISE_PER_CLUSTER:
                        break
                    prompt = _DEDUP_PROMPT.format(
                        mem_a=cluster[i].content,
                        mem_b=cluster[j].content,
                    )
                    try:
                        raw = llm_complete(prompt)
                        data = extract_json_or(raw, {}, expect="object")
                        if data.get("is_duplicate", False):
                            duplicates.append((i, j))
                    except Exception:
                        continue
                    pair_count += 1
                if pair_count >= ContinualRefinement._MAX_PAIRWISE_PER_CLUSTER:
                    break

            if not duplicates:
                continue

            dup_indices = set()
            for i, j in duplicates:
                dup_indices.add(i)
                dup_indices.add(j)

            to_merge = [cluster[idx] for idx in sorted(dup_indices)]
            memories_text = "\n".join(f"- {m.content}" for m in to_merge)

            try:
                raw = llm_complete(_MERGE_PROMPT.format(memories=memories_text))
                data = extract_json_or(raw, {}, expect="object")
                merged_content = data.get("merged", "")
                if not merged_content:
                    continue

                new_memory = Memory(
                    content=merged_content,
                    layer="semantic",
                    category=data.get("category", to_merge[0].category),
                    tags=data.get("tags", to_merge[0].tags),
                    entities=to_merge[0].entities,
                    embedding=emb.encode_document(merged_content),
                    source="refinement",
                )
                db.insert(new_memory)

                for m in to_merge:
                    db.deactivate(m.id)

                merged_count += 1
            except Exception as exc:
                _log.warning("Merge failed for cluster: %s", exc)

        return merged_count

    @staticmethod
    def _prune_low_quality(
        db: "MemoryDB",
        llm_complete: Callable[[str], str],
        batch_size: int = 20,
    ) -> int:
        """Apply inclusion bar to recent non-refined L2 memories."""
        rows = db.query(
            "SELECT * FROM memories FINAL "
            "WHERE layer = 'semantic' AND is_active = 1 "
            "AND source != 'refinement' "
            "ORDER BY created_at DESC "
            f"LIMIT {batch_size}"
        )
        if not rows:
            return 0

        pruned = 0
        for row in rows:
            content = row.get("content", "")
            mem_id = row.get("id", "")
            if not content or not mem_id:
                continue
            try:
                raw = llm_complete(_INCLUSION_PROMPT.format(content=content))
                data = extract_json_or(raw, {}, expect="object")
                if not data.get("keep", True):
                    db.deactivate(mem_id)
                    pruned += 1
            except Exception:
                continue

        return pruned
