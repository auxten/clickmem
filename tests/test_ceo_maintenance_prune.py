"""Tests for CEO Maintenance — pruning and outcome validation."""

from datetime import datetime, timedelta, timezone

from memory_core.ceo_maintenance import CEOMaintenance
from tests.helpers.factories import DecisionFactory, PrincipleFactory


class TestPruneWeakPrinciples:

    def test_prune_old_weak_principles(self, ceo_db, mock_emb):
        """Principles with evidence=1, low confidence, and old age get pruned."""
        old_date = datetime.now(timezone.utc) - timedelta(days=60)

        # Weak: will be pruned
        weak = PrincipleFactory.build(
            content="Use not string instead of string.empty",
            confidence=0.6, evidence_count=1,
        )
        weak.embedding = mock_emb.encode_document(weak.content)
        ceo_db.insert_principle(weak)
        # Manually set created_at to old
        ceo_db._session.query(
            f"ALTER TABLE principles UPDATE created_at = '{old_date.strftime('%Y-%m-%d %H:%M:%S.000')}' "
            f"WHERE id = '{weak.id}'"
        )
        ceo_db._session.query("OPTIMIZE TABLE principles FINAL")

        # Strong: will NOT be pruned (high evidence)
        strong = PrincipleFactory.build(
            content="Always validate SQL injection parameters",
            confidence=0.9, evidence_count=5,
        )
        strong.embedding = mock_emb.encode_document(strong.content)
        ceo_db.insert_principle(strong)
        ceo_db._session.query(
            f"ALTER TABLE principles UPDATE created_at = '{old_date.strftime('%Y-%m-%d %H:%M:%S.000')}' "
            f"WHERE id = '{strong.id}'"
        )
        ceo_db._session.query("OPTIMIZE TABLE principles FINAL")

        pruned = CEOMaintenance.prune_weak_principles(ceo_db, min_age_days=30)
        pruned_ids = {p["id"] for p in pruned}

        assert weak.id in pruned_ids
        assert strong.id not in pruned_ids

    def test_prune_dry_run_does_not_deactivate(self, ceo_db, mock_emb):
        """Dry run lists candidates but doesn't deactivate."""
        old_date = datetime.now(timezone.utc) - timedelta(days=60)
        weak = PrincipleFactory.build(confidence=0.6, evidence_count=1)
        weak.embedding = mock_emb.encode_document(weak.content)
        ceo_db.insert_principle(weak)
        ceo_db._session.query(
            f"ALTER TABLE principles UPDATE created_at = '{old_date.strftime('%Y-%m-%d %H:%M:%S.000')}' "
            f"WHERE id = '{weak.id}'"
        )
        ceo_db._session.query("OPTIMIZE TABLE principles FINAL")

        pruned = CEOMaintenance.prune_weak_principles(ceo_db, min_age_days=30, dry_run=True)
        assert len(pruned) >= 1

        # Verify principle is still active
        p = ceo_db.get_principle(weak.id)
        assert p is not None
        assert p.is_active is True

    def test_prune_preserves_young_principles(self, ceo_db, mock_emb):
        """Recent principles should not be pruned even if weak."""
        young = PrincipleFactory.build(confidence=0.6, evidence_count=1)
        young.embedding = mock_emb.encode_document(young.content)
        ceo_db.insert_principle(young)

        pruned = CEOMaintenance.prune_weak_principles(ceo_db, min_age_days=30)
        pruned_ids = {p["id"] for p in pruned}
        assert young.id not in pruned_ids


class TestUpdateOutcome:

    def test_ceo_update_outcome(self, ceo_db):
        """ceo_update_outcome updates decision status."""
        from memory_core.ceo_skills import ceo_update_outcome

        d = DecisionFactory.build(outcome_status="pending")
        ceo_db.insert_decision(d)

        result = ceo_update_outcome(ceo_db, d.id, "validated", "Worked well in prod")
        assert result["new_status"] == "validated"
        assert result["old_status"] == "pending"

        updated = ceo_db.get_decision(d.id)
        assert updated.outcome_status == "validated"

    def test_ceo_update_outcome_invalid_status(self, ceo_db):
        """Invalid status should return an error."""
        from memory_core.ceo_skills import ceo_update_outcome

        d = DecisionFactory.build()
        ceo_db.insert_decision(d)

        result = ceo_update_outcome(ceo_db, d.id, "bad_status")
        assert "error" in result

    def test_ceo_update_outcome_not_found(self, ceo_db):
        """Non-existent decision should return an error."""
        from memory_core.ceo_skills import ceo_update_outcome

        result = ceo_update_outcome(ceo_db, "nonexistent-id", "validated")
        assert "error" in result
