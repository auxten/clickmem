"""Tests for the 5 maintenance functions.

Covers cleanup_stale, purge_deleted, compress_episodic,
promote_to_semantic, and review_semantic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from memory_core import MemoryDB, maintenance
from memory_core.models import Memory
from tests.helpers.factories import (
    make_memory,
    seed_stale_episodic,
    seed_episodic,
    seed_semantic,
    seed_with_repeated_tag,
)
from tests.helpers.assertions import (
    assert_memory_inactive,
    assert_memory_active,
    assert_all_layer,
    assert_layer_count,
)


class TestCleanupStale:
    """Test maintenance.cleanup_stale — removes long-unaccessed L1 entries."""

    @freeze_time("2026-03-04")
    def test_cleans_stale_entries(self, db):
        """Entries older than decay_days with 0 accesses are deactivated."""
        for m in seed_stale_episodic(3, stale_days=130):
            db.insert(m)
        cleaned = maintenance.cleanup_stale(db, decay_days=120)
        assert cleaned == 3

    @freeze_time("2026-03-04")
    def test_preserves_accessed_entries(self, db):
        """Entries with access_count > 0 are preserved even if old."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        m = make_memory(
            layer="episodic",
            access_count=5,
            created_at=now - timedelta(days=200),
            accessed_at=now - timedelta(days=200),
        )
        db.insert(m)
        cleaned = maintenance.cleanup_stale(db, decay_days=120)
        assert cleaned == 0
        assert db.get(m.id) is not None
        assert_memory_active(db.get(m.id))

    @freeze_time("2026-03-04")
    def test_preserves_recent_entries(self, db):
        """Recent entries (within decay_days) are preserved."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        m = make_memory(
            layer="episodic",
            access_count=0,
            created_at=now - timedelta(days=10),
            accessed_at=now - timedelta(days=10),
        )
        db.insert(m)
        cleaned = maintenance.cleanup_stale(db, decay_days=120)
        assert cleaned == 0

    @freeze_time("2026-03-04")
    def test_does_not_touch_semantic(self, db):
        """cleanup_stale only affects episodic layer."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        m = make_memory(
            layer="semantic",
            access_count=0,
            created_at=now - timedelta(days=200),
            accessed_at=now - timedelta(days=200),
        )
        db.insert(m)
        cleaned = maintenance.cleanup_stale(db, decay_days=120)
        assert cleaned == 0
        assert_memory_active(db.get(m.id))

    def test_returns_zero_on_empty_db(self, db):
        """cleanup_stale returns 0 on empty database."""
        cleaned = maintenance.cleanup_stale(db, decay_days=120)
        assert cleaned == 0

    @freeze_time("2026-03-04")
    def test_custom_decay_days(self, db):
        """cleanup_stale respects custom decay_days."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        m = make_memory(
            layer="episodic",
            access_count=0,
            created_at=now - timedelta(days=50),
            accessed_at=now - timedelta(days=50),
        )
        db.insert(m)
        # 30-day threshold should catch this entry
        cleaned = maintenance.cleanup_stale(db, decay_days=30)
        assert cleaned == 1


class TestPurgeDeleted:
    """Test maintenance.purge_deleted — physically removes soft-deleted entries."""

    @freeze_time("2026-03-04")
    def test_purges_old_deleted(self, db):
        """Entries soft-deleted more than `days` ago are physically removed."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        m = make_memory(is_active=False, updated_at=now - timedelta(days=10))
        db.insert(m)
        purged = maintenance.purge_deleted(db, days=7)
        assert purged == 1
        assert db.get(m.id) is None

    @freeze_time("2026-03-04")
    def test_preserves_recently_deleted(self, db):
        """Recently soft-deleted entries are not purged."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        m = make_memory(is_active=False, updated_at=now - timedelta(days=2))
        db.insert(m)
        purged = maintenance.purge_deleted(db, days=7)
        assert purged == 0

    def test_preserves_active_entries(self, db):
        """Active entries are never purged."""
        m = make_memory(is_active=True)
        db.insert(m)
        purged = maintenance.purge_deleted(db, days=7)
        assert purged == 0
        assert db.get(m.id) is not None

    def test_returns_zero_on_empty(self, db):
        """purge_deleted returns 0 on empty database."""
        purged = maintenance.purge_deleted(db, days=7)
        assert purged == 0


class TestCompressEpisodic:
    """Test maintenance.compress_episodic — monthly L1 compression."""

    def test_compress_creates_summary(self, db, mock_llm, mock_emb):
        """Compressing a month creates a summary entry."""
        for i in range(10):
            m = make_memory(
                layer="episodic",
                content=f"Event {i} in January",
                created_at=datetime(2026, 1, 15 + (i % 15), tzinfo=timezone.utc),
                embedding=mock_emb.encode_document(f"Event {i} in January"),
            )
            db.insert(m)
        compressed = maintenance.compress_episodic(db, mock_llm, mock_emb, month="2026-01")
        assert compressed >= 1

    def test_compress_deactivates_originals(self, db, mock_llm, mock_emb):
        """After compression, original entries are deactivated."""
        entries = []
        for i in range(5):
            m = make_memory(
                layer="episodic",
                content=f"January event {i}",
                created_at=datetime(2026, 1, 10 + i, tzinfo=timezone.utc),
                embedding=mock_emb.encode_document(f"January event {i}"),
            )
            db.insert(m)
            entries.append(m)
        maintenance.compress_episodic(db, mock_llm, mock_emb, month="2026-01")
        for m in entries:
            stored = db.get(m.id)
            assert_memory_inactive(stored)

    def test_compress_calls_llm(self, db, mock_llm, mock_emb):
        """compress_episodic calls the LLM to generate a summary."""
        for i in range(3):
            m = make_memory(
                layer="episodic",
                created_at=datetime(2026, 1, 10 + i, tzinfo=timezone.utc),
                embedding=mock_emb.encode_document(f"event {i}"),
            )
            db.insert(m)
        maintenance.compress_episodic(db, mock_llm, mock_emb, month="2026-01")
        assert mock_llm.call_count >= 1

    def test_compress_empty_month_returns_zero(self, db, mock_llm, mock_emb):
        """Compressing a month with no entries returns 0."""
        compressed = maintenance.compress_episodic(db, mock_llm, mock_emb, month="2026-06")
        assert compressed == 0

    def test_compress_preserves_other_months(self, db, mock_llm, mock_emb):
        """Compressing January does not affect February entries."""
        jan = make_memory(
            layer="episodic",
            created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
            embedding=mock_emb.encode_document("jan"),
        )
        feb = make_memory(
            layer="episodic",
            created_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
            embedding=mock_emb.encode_document("feb"),
        )
        db.insert(jan)
        db.insert(feb)
        maintenance.compress_episodic(db, mock_llm, mock_emb, month="2026-01")
        assert_memory_active(db.get(feb.id))

    def test_summary_has_source_maintenance(self, db, mock_llm, mock_emb):
        """The generated summary memory has source='maintenance'."""
        for i in range(3):
            m = make_memory(
                layer="episodic",
                created_at=datetime(2026, 1, 10 + i, tzinfo=timezone.utc),
                embedding=mock_emb.encode_document(f"event {i}"),
            )
            db.insert(m)
        maintenance.compress_episodic(db, mock_llm, mock_emb, month="2026-01")
        # Find the summary entry (it should be active and in episodic)
        active = db.list_by_layer("episodic")
        summaries = [m for m in active if m.source == "maintenance"]
        assert len(summaries) >= 1


class TestPromoteToSemantic:
    """Test maintenance.promote_to_semantic — L1 -> L2 promotion."""

    def test_promotes_recurring_tag(self, db, mock_llm, mock_emb):
        """Tags appearing >= 3 times trigger promotion."""
        for m in seed_with_repeated_tag("microservices", count=4):
            m.embedding = mock_emb.encode_document(m.content)
            db.insert(m)
        promoted = maintenance.promote_to_semantic(db, mock_llm, mock_emb)
        assert promoted >= 1

    def test_promoted_entry_is_semantic(self, db, mock_llm, mock_emb):
        """Promoted entries are created in the semantic layer."""
        for m in seed_with_repeated_tag("microservices", count=4):
            m.embedding = mock_emb.encode_document(m.content)
            db.insert(m)
        maintenance.promote_to_semantic(db, mock_llm, mock_emb)
        semantic = db.list_by_layer("semantic")
        assert len(semantic) >= 1

    def test_promotion_calls_llm(self, db, mock_llm, mock_emb):
        """promote_to_semantic calls LLM to decide on promotion."""
        for m in seed_with_repeated_tag("microservices", count=4):
            m.embedding = mock_emb.encode_document(m.content)
            db.insert(m)
        maintenance.promote_to_semantic(db, mock_llm, mock_emb)
        assert mock_llm.call_count >= 1

    def test_no_promotion_below_threshold(self, db, mock_llm, mock_emb):
        """Tags appearing < 3 times don't trigger promotion."""
        m = make_memory(
            layer="episodic",
            tags=["rare-topic"],
            embedding=mock_emb.encode_document("rare"),
        )
        db.insert(m)
        promoted = maintenance.promote_to_semantic(db, mock_llm, mock_emb)
        assert promoted == 0

    def test_promoted_entry_has_source_maintenance(self, db, mock_llm, mock_emb):
        """Promoted semantic entries have source='maintenance'."""
        for m in seed_with_repeated_tag("microservices", count=4):
            m.embedding = mock_emb.encode_document(m.content)
            db.insert(m)
        maintenance.promote_to_semantic(db, mock_llm, mock_emb)
        semantic = db.list_by_layer("semantic")
        promoted = [m for m in semantic if m.source == "maintenance"]
        assert len(promoted) >= 1

    def test_returns_zero_on_empty(self, db, mock_llm, mock_emb):
        """promote_to_semantic returns 0 on empty database."""
        promoted = maintenance.promote_to_semantic(db, mock_llm, mock_emb)
        assert promoted == 0


class TestReviewSemantic:
    """Test maintenance.review_semantic — L2 staleness review."""

    def test_review_returns_count(self, db, mock_llm):
        """review_semantic returns the number of entries reviewed."""
        for m in seed_semantic(3):
            db.insert(m)
        reviewed = maintenance.review_semantic(db, mock_llm)
        assert isinstance(reviewed, int)

    def test_review_calls_llm(self, db, mock_llm):
        """review_semantic calls the LLM for review."""
        for m in seed_semantic(3):
            db.insert(m)
        maintenance.review_semantic(db, mock_llm)
        assert mock_llm.call_count >= 1

    def test_review_empty_returns_zero(self, db, mock_llm):
        """review_semantic returns 0 when no semantic memories exist."""
        reviewed = maintenance.review_semantic(db, mock_llm)
        assert reviewed == 0


class TestRunAll:
    """Test maintenance.run_all — orchestrator."""

    def test_run_all_returns_dict(self, db, mock_llm, mock_emb):
        """run_all returns a summary dict."""
        result = maintenance.run_all(db, llm_complete=mock_llm, emb=mock_emb)
        assert isinstance(result, dict)

    def test_run_all_has_expected_keys(self, db, mock_llm, mock_emb):
        """run_all result has keys for each maintenance step."""
        result = maintenance.run_all(db, llm_complete=mock_llm, emb=mock_emb)
        expected_keys = {"stale_cleaned", "deleted_purged", "compressed", "promoted", "reviewed"}
        assert expected_keys.issubset(set(result.keys()))

    @freeze_time("2026-03-04")
    def test_run_all_handles_populated_db(self, populated_db, mock_llm, mock_emb):
        """run_all can process a populated database without errors."""
        result = maintenance.run_all(populated_db, llm_complete=mock_llm, emb=mock_emb)
        assert isinstance(result, dict)
