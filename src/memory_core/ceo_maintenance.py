"""CEO Maintenance — background maintenance tasks for CEO Brain entities.

Replaces maintenance_mod.py with CEO-specific operations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB

logger = logging.getLogger(__name__)


class CEOMaintenance:
    """Maintenance operations for CEO Brain entities."""

    @staticmethod
    def run_all(ceo_db: CeoDB, llm_complete: Callable | None = None, emb=None) -> dict:
        """Run all maintenance tasks. Returns stats dict."""
        result = {}

        expired = CEOMaintenance.expire_episodes(ceo_db)
        result["episodes_expired"] = expired

        if emb:
            deduped = CEOMaintenance.dedup_principles(ceo_db, emb, llm_complete)
            result["principles_deduped"] = deduped

            pruned = CEOMaintenance.prune_weak_principles(ceo_db)
            result["principles_pruned"] = len(pruned)

        if llm_complete:
            promoted = CEOMaintenance.check_principle_promotion(ceo_db, llm_complete, emb)
            result["principles_promoted"] = promoted

            validated = CEOMaintenance.validate_decision_outcomes(ceo_db, llm_complete)
            result["decisions_validated"] = validated

        return result

    @staticmethod
    def expire_episodes(ceo_db: CeoDB, ttl_days: int = 180) -> int:
        """Delete episodes older than TTL. ChDB TTL handles most, this is a manual cleanup."""
        try:
            rows = ceo_db.query(
                f"SELECT count() as cnt FROM episodes "
                f"WHERE created_at < now() - INTERVAL {ttl_days} DAY"
            )
            count = int(rows[0]["cnt"]) if rows else 0
            if count > 0:
                ceo_db._session.query(
                    f"ALTER TABLE episodes DELETE "
                    f"WHERE created_at < now() - INTERVAL {ttl_days} DAY"
                )
                logger.info("Expired %d episodes older than %d days", count, ttl_days)
            return count
        except Exception as e:
            logger.warning("Failed to expire episodes: %s", e)
            return 0

    @staticmethod
    def check_principle_promotion(
        ceo_db: CeoDB,
        llm_complete: Callable,
        emb=None,
        evidence_threshold: int = 3,
    ) -> int:
        """Find principles with high evidence but low confidence, consider promoting."""
        principles = ceo_db.list_principles(active_only=True)
        promoted = 0
        for p in principles:
            if p.evidence_count >= evidence_threshold and p.confidence < 0.7:
                # Bump confidence based on evidence
                new_confidence = min(0.9, 0.5 + 0.1 * p.evidence_count)
                if new_confidence > p.confidence:
                    ceo_db.update_principle(p.id, confidence=new_confidence)
                    promoted += 1
        return promoted

    @staticmethod
    def validate_decision_outcomes(
        ceo_db: CeoDB, llm_complete: Callable, emb=None,
    ) -> int:
        """Review pending decisions older than 30 days using semantic matching."""
        decisions = ceo_db.list_decisions(limit=50)
        validated = 0
        for d in decisions:
            if d.outcome_status != "pending":
                continue
            if not d.created_at:
                continue
            age_days = (datetime.now(timezone.utc) - d.created_at).total_seconds() / 86400
            if age_days < 30:
                continue

            # Try semantic matching: search episodes for this decision's topic
            if emb and d.embedding:
                related = ceo_db.search_episodes_by_vector(
                    d.embedding, project_id=d.project_id, limit=5,
                )
                if related:
                    # Ask LLM to judge the outcome
                    ep_text = "\n".join(f"- {e.content[:150]}" for e in related[:3])
                    prompt = (
                        f"Decision: {d.title}\nChoice: {d.choice}\n\n"
                        f"Recent related activity:\n{ep_text}\n\n"
                        f"Based on the activity, was this decision validated (worked well), "
                        f"invalidated (caused problems), or unknown (insufficient evidence)?\n"
                        f"Respond with one word: validated, invalidated, or unknown"
                    )
                    try:
                        answer = llm_complete(prompt).strip().lower()
                        if answer in ("validated", "invalidated"):
                            ceo_db.update_decision(d.id, outcome_status=answer)
                            validated += 1
                            continue
                    except Exception:
                        pass

            # Fallback: mark as unknown for very old pending decisions
            if age_days > 90:
                ceo_db.update_decision(d.id, outcome_status="unknown")
                validated += 1
        return validated

    @staticmethod
    def prune_weak_principles(
        ceo_db: CeoDB,
        min_age_days: int = 30,
        dry_run: bool = False,
        project_id: str | None = None,
    ) -> list[dict]:
        """Deactivate principles with evidence_count<=1, confidence<0.75, age>min_age_days.

        Returns list of pruned principle dicts for audit.
        """
        principles = ceo_db.list_principles(
            project_id=project_id, active_only=True,
        )
        pruned: list[dict] = []
        now = datetime.now(timezone.utc)

        for p in principles:
            if p.evidence_count > 1:
                continue
            if p.confidence >= 0.75:
                continue
            if not p.created_at:
                continue
            age_days = (now - p.created_at).total_seconds() / 86400
            if age_days < min_age_days:
                continue

            entry = {
                "id": p.id,
                "content": p.content[:100],
                "confidence": p.confidence,
                "evidence_count": p.evidence_count,
                "age_days": int(age_days),
                "domain": p.domain,
            }
            pruned.append(entry)

            if not dry_run:
                ceo_db.update_principle(p.id, is_active=False)
                logger.info("Pruned weak principle %s: %s", p.id[:8], p.content[:60])

        logger.info("Pruned %d weak principles (dry_run=%s)", len(pruned), dry_run)
        return pruned

    @staticmethod
    def dedup_principles(
        ceo_db: CeoDB,
        emb,
        llm_complete: Callable | None = None,
    ) -> int:
        """Find near-duplicate principles and merge (combine evidence_count)."""
        principles = ceo_db.list_principles(active_only=True)
        merged = 0
        seen_ids: set[str] = set()

        for i, p in enumerate(principles):
            if p.id in seen_ids or not p.embedding:
                continue
            for j in range(i + 1, len(principles)):
                q = principles[j]
                if q.id in seen_ids or not q.embedding:
                    continue
                dist = ceo_db._cosine_dist(p.embedding, q.embedding)
                if dist < 0.15:  # very similar
                    # Merge: keep p, deactivate q, sum evidence
                    new_evidence = p.evidence_count + q.evidence_count
                    new_confidence = max(p.confidence, q.confidence)
                    ceo_db.update_principle(p.id, evidence_count=new_evidence, confidence=new_confidence)
                    ceo_db.update_principle(q.id, is_active=False)
                    seen_ids.add(q.id)
                    merged += 1

        return merged

    @staticmethod
    def detect_contradictions(
        ceo_db: CeoDB,
        emb,
        llm_complete: Callable | None = None,
    ) -> list[dict]:
        """Find pairs of principles that may contradict each other."""
        # This is a placeholder — full implementation would use LLM
        return []
