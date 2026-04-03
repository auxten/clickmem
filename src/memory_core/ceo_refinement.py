"""CEO Refinement — continual refinement for CEO Brain entities.

Replaces refinement.py. Handles re-extraction from unprocessed raws,
episode dedup, decision consolidation, and principle consolidation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB
    from memory_core.db import MemoryDB

logger = logging.getLogger(__name__)


class CEORefinement:
    """Continual refinement operations for CEO Brain."""

    @staticmethod
    def run(ceo_db: CeoDB, emb, llm_complete: Callable, old_db=None) -> dict:
        """Full refinement pass."""
        result = {}

        if old_db:
            reextracted = CEORefinement.reextract_unprocessed(ceo_db, emb, llm_complete, old_db)
            result["reextracted"] = reextracted

        deduped_ep = CEORefinement.dedup_episodes(ceo_db, emb)
        result["episodes_deduped"] = deduped_ep

        consolidated_d = CEORefinement.consolidate_decisions(ceo_db, emb, llm_complete)
        result["decisions_consolidated"] = consolidated_d

        consolidated_p = CEORefinement.consolidate_principles(ceo_db, emb, llm_complete)
        result["principles_consolidated"] = consolidated_p

        return result

    @staticmethod
    def reextract_unprocessed(ceo_db: CeoDB, emb, llm_complete: Callable, old_db) -> int:
        """Re-extract from unprocessed raw_transcripts using CEO extractor."""
        from memory_core.ceo_extractor import CEOExtractor
        from memory_core.conversation_filter import filter_conversation

        unprocessed = old_db.list_unprocessed_raw(limit=50)
        if not unprocessed:
            return 0

        extractor = CEOExtractor(ceo_db, emb)
        count = 0

        for raw in unprocessed:
            content = raw.get("content", "")
            if not content:
                old_db.mark_raw_processed(raw["id"])
                continue

            filtered = filter_conversation([{"role": "user", "content": content}])
            result = extractor.extract(
                filtered, llm_complete,
                session_id=raw.get("session_id", ""),
                raw_id=raw["id"],
            )
            entity_count = len(result.episode_ids) + len(result.decision_ids) + len(result.principle_ids)
            if entity_count > 0:
                old_db.mark_raw_processed(raw["id"])
                count += entity_count

        return count

    @staticmethod
    def dedup_episodes(ceo_db: CeoDB, emb) -> int:
        """Remove near-duplicate episodes."""
        episodes = ceo_db.list_episodes(limit=100)
        removed = 0
        seen_ids: set[str] = set()

        for i, e in enumerate(episodes):
            if e.id in seen_ids or not e.embedding:
                continue
            for j in range(i + 1, len(episodes)):
                other = episodes[j]
                if other.id in seen_ids or not other.embedding:
                    continue
                dist = ceo_db._cosine_dist(e.embedding, other.embedding)
                if dist < 0.05:  # nearly identical
                    # Keep the newer one (earlier in list since sorted desc)
                    # Delete the older one via ALTER TABLE DELETE
                    try:
                        ceo_db._session.query(
                            f"ALTER TABLE episodes DELETE WHERE id = '{ceo_db._escape(other.id)}'"
                        )
                        seen_ids.add(other.id)
                        removed += 1
                    except Exception as exc:
                        logger.warning("Failed to delete duplicate episode: %s", exc)

        return removed

    @staticmethod
    def consolidate_decisions(ceo_db: CeoDB, emb, llm_complete: Callable) -> int:
        """Find near-duplicate decisions and merge."""
        from memory_core.ceo_dedup import dedup_decision

        decisions = ceo_db.list_decisions(limit=100)
        consolidated = 0

        for i, d in enumerate(decisions):
            if not d.embedding:
                continue
            for j in range(i + 1, len(decisions)):
                other = decisions[j]
                if not other.embedding:
                    continue
                dist = ceo_db._cosine_dist(d.embedding, other.embedding)
                if dist < 0.1:
                    # Merge: keep d (newer), update with info from other
                    if other.outcome and not d.outcome:
                        ceo_db.update_decision(d.id, outcome=other.outcome,
                                               outcome_status=other.outcome_status)
                    consolidated += 1

        return consolidated

    @staticmethod
    def consolidate_principles(ceo_db: CeoDB, emb, llm_complete: Callable) -> int:
        """Merge near-duplicate principles, sum evidence_counts."""
        from memory_core.ceo_maintenance import CEOMaintenance
        return CEOMaintenance.dedup_principles(ceo_db, emb, llm_complete)
