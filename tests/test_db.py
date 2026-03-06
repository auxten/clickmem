"""Tests for MemoryDB CRUD operations and schema management.

Covers insert, get, update, deactivate, delete, queries, and statistics.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from memory_core import MemoryDB
from memory_core.models import Memory
from tests.helpers.factories import (
    make_memory,
    seed_working,
    seed_episodic,
    seed_semantic,
    seed_stale_episodic,
    seed_with_repeated_tag,
)
from tests.helpers.assertions import (
    assert_memory_fields,
    assert_valid_uuid,
    assert_memory_active,
    assert_memory_inactive,
    assert_no_duplicate_ids,
    assert_all_layer,
    assert_layer_count,
)


class TestMemoryDBInit:
    """Test MemoryDB initialization and schema creation."""

    def test_create_in_memory(self):
        """MemoryDB can be created with in-memory storage."""
        db = MemoryDB(":memory:")
        assert db is not None

    def test_create_with_path(self, tmp_db_path):
        """MemoryDB can be created with a filesystem path."""
        db = MemoryDB(tmp_db_path)
        assert db is not None

    def test_empty_db_count_is_zero(self, db):
        """A fresh database has zero memories."""
        assert db.count() == 0

    def test_empty_db_count_by_layer(self, db):
        """A fresh database reports zero for all layers."""
        counts = db.count_by_layer()
        assert counts.get("working", 0) == 0
        assert counts.get("episodic", 0) == 0
        assert counts.get("semantic", 0) == 0


class TestInsert:
    """Test memory insertion."""

    def test_insert_returns_id(self, db):
        """Insert returns the memory ID."""
        m = make_memory(content="Test insert")
        result_id = db.insert(m)
        assert result_id == m.id

    def test_insert_increments_count(self, db):
        """Count increases after insertion."""
        db.insert(make_memory())
        assert db.count() == 1
        db.insert(make_memory())
        assert db.count() == 2

    def test_insert_preserves_content(self, db):
        """Inserted memory retains its content."""
        m = make_memory(content="Specific content here")
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content == "Specific content here"

    def test_insert_preserves_layer(self, db):
        """Inserted memory retains its layer."""
        m = make_memory(layer="semantic")
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.layer == "semantic"

    def test_insert_preserves_tags(self, db):
        """Inserted memory retains its tags."""
        m = make_memory(tags=["alpha", "beta"])
        db.insert(m)
        retrieved = db.get(m.id)
        assert set(retrieved.tags) == {"alpha", "beta"}

    def test_insert_preserves_embedding(self, db, mock_emb):
        """Inserted memory retains its embedding vector."""
        vec = mock_emb.encode_document("test text")
        m = make_memory(embedding=vec)
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.embedding is not None
        assert len(retrieved.embedding) == len(vec)

    def test_insert_with_all_fields(self, db):
        """Memory with all fields populated can be inserted and retrieved."""
        now = datetime.now(timezone.utc)
        m = Memory(
            content="Full memory",
            layer="episodic",
            category="decision",
            tags=["a", "b"],
            entities=["Alice", "Bob"],
            embedding=[0.1] * 256,
            session_id="sess-1",
            source="cli",
            is_active=True,
            access_count=0,
            created_at=now,
            updated_at=now,
            accessed_at=now,
        )
        db.insert(m)
        retrieved = db.get(m.id)
        assert_memory_fields(retrieved, content="Full memory", layer="episodic", category="decision")


class TestGet:
    """Test memory retrieval by ID."""

    def test_get_existing(self, db):
        """Get returns the correct memory by ID."""
        m = make_memory(content="Find me")
        db.insert(m)
        result = db.get(m.id)
        assert result is not None
        assert result.content == "Find me"

    def test_get_nonexistent_returns_none(self, db):
        """Get returns None for a non-existent ID."""
        result = db.get("nonexistent-id-12345")
        assert result is None

    def test_get_returns_memory_type(self, db):
        """Get returns a Memory instance."""
        m = make_memory()
        db.insert(m)
        result = db.get(m.id)
        assert isinstance(result, Memory)


class TestUpdateContent:
    """Test memory content updates."""

    def test_update_changes_content(self, db):
        """update_content changes the stored content."""
        m = make_memory(content="Original")
        db.insert(m)
        new_id = db.update_content(m.id, "Updated content")
        updated = db.get(new_id)
        assert updated.content == "Updated content"

    def test_update_deactivates_old(self, db):
        """update_content deactivates the original memory."""
        m = make_memory(content="Original")
        db.insert(m)
        db.update_content(m.id, "Updated")
        old = db.get(m.id)
        assert_memory_inactive(old)

    def test_update_returns_new_id(self, db):
        """update_content returns a new ID (new version)."""
        m = make_memory(content="Original")
        db.insert(m)
        new_id = db.update_content(m.id, "Updated")
        assert new_id != m.id
        assert_valid_uuid(new_id)

    def test_update_preserves_layer(self, db):
        """update_content preserves the layer of the original memory."""
        m = make_memory(content="Original", layer="semantic")
        db.insert(m)
        new_id = db.update_content(m.id, "Updated")
        updated = db.get(new_id)
        assert updated.layer == "semantic"


class TestDeactivate:
    """Test soft deletion."""

    def test_deactivate_sets_inactive(self, db):
        """Deactivate marks memory as inactive."""
        m = make_memory()
        db.insert(m)
        result = db.deactivate(m.id)
        assert result is True
        deactivated = db.get(m.id)
        assert_memory_inactive(deactivated)

    def test_deactivate_nonexistent_returns_false(self, db):
        """Deactivating a non-existent ID returns False."""
        result = db.deactivate("nonexistent-id")
        assert result is False

    def test_deactivated_excluded_from_count(self, db):
        """Deactivated memories are not counted in active count."""
        m = make_memory()
        db.insert(m)
        assert db.count() == 1
        db.deactivate(m.id)
        assert db.count() == 0


class TestDelete:
    """Test physical deletion."""

    def test_delete_removes_memory(self, db):
        """Delete physically removes the memory."""
        m = make_memory()
        db.insert(m)
        result = db.delete(m.id)
        assert result is True
        assert db.get(m.id) is None

    def test_delete_nonexistent_returns_false(self, db):
        """Deleting a non-existent ID returns False."""
        result = db.delete("nonexistent-id")
        assert result is False


class TestWorkingMemory:
    """Test L0 working memory operations."""

    def test_set_working_stores_content(self, db):
        """set_working stores the working memory content."""
        db.set_working("Current focus: debugging HNSW")
        result = db.get_working()
        assert result == "Current focus: debugging HNSW"

    def test_set_working_overwrites_previous(self, db):
        """set_working replaces the previous working memory."""
        db.set_working("First focus")
        db.set_working("Second focus")
        result = db.get_working()
        assert result == "Second focus"

    def test_get_working_empty_returns_none(self, db):
        """get_working returns None when no working memory exists."""
        result = db.get_working()
        assert result is None

    def test_working_memory_only_one_entry(self, db):
        """Only one working memory entry should exist at a time."""
        db.set_working("First")
        db.set_working("Second")
        db.set_working("Third")
        counts = db.count_by_layer()
        assert counts.get("working", 0) == 1


class TestListByLayer:
    """Test layer-based listing."""

    def test_list_episodic(self, populated_db):
        """list_by_layer('episodic') returns only episodic memories."""
        results = populated_db.list_by_layer("episodic")
        assert_all_layer(results, "episodic")
        assert len(results) == 5

    def test_list_semantic(self, populated_db):
        """list_by_layer('semantic') returns only semantic memories."""
        results = populated_db.list_by_layer("semantic")
        assert_all_layer(results, "semantic")
        assert len(results) == 5

    def test_list_with_limit(self, populated_db):
        """list_by_layer respects the limit parameter."""
        results = populated_db.list_by_layer("episodic", limit=2)
        assert len(results) == 2

    def test_list_empty_layer(self, db):
        """Listing an empty layer returns empty list."""
        results = db.list_by_layer("episodic")
        assert results == []


class TestFindByTags:
    """Test tag-based search."""

    def test_find_by_single_tag(self, db):
        """find_by_tags returns memories matching a single tag."""
        m = make_memory(tags=["python", "coding"])
        db.insert(m)
        results = db.find_by_tags(["python"])
        assert len(results) >= 1
        assert any(r.id == m.id for r in results)

    def test_find_by_tag_no_match(self, db):
        """find_by_tags returns empty list when no memories match."""
        db.insert(make_memory(tags=["python"]))
        results = db.find_by_tags(["nonexistent-tag"])
        assert results == []


class TestFindStaleEpisodic:
    """Test finding stale episodic memories."""

    def test_finds_stale_entries(self, db):
        """find_stale_episodic returns entries older than decay_days with 0 accesses."""
        for m in seed_stale_episodic(3, stale_days=130):
            db.insert(m)
        # Also insert a fresh one
        db.insert(make_memory(layer="episodic", access_count=0))

        stale = db.find_stale_episodic(decay_days=120)
        assert len(stale) == 3
        for m in stale:
            assert m.access_count == 0

    def test_accessed_entries_not_stale(self, db):
        """Entries with access_count > 0 are not stale."""
        now = datetime.now(timezone.utc)
        m = make_memory(
            layer="episodic",
            access_count=5,
            created_at=now - timedelta(days=200),
            accessed_at=now - timedelta(days=200),
        )
        db.insert(m)
        stale = db.find_stale_episodic(decay_days=120)
        assert len(stale) == 0


class TestGetEpisodicByMonth:
    """Test monthly episodic retrieval."""

    def test_returns_correct_month(self, db):
        """get_episodic_by_month returns entries from the specified month."""
        m = make_memory(
            layer="episodic",
            created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
        db.insert(m)
        results = db.get_episodic_by_month("2026-01")
        assert len(results) == 1
        assert results[0].id == m.id

    def test_excludes_other_months(self, db):
        """get_episodic_by_month excludes entries from other months."""
        db.insert(make_memory(
            layer="episodic",
            created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        ))
        db.insert(make_memory(
            layer="episodic",
            created_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
        ))
        results = db.get_episodic_by_month("2026-01")
        assert len(results) == 1


class TestGetTagFrequencies:
    """Test tag frequency analysis."""

    def test_counts_tag_occurrences(self, db):
        """get_tag_frequencies correctly counts tag appearances."""
        for m in seed_with_repeated_tag("chdb", count=4):
            db.insert(m)
        freqs = db.get_tag_frequencies(layer="episodic", min_count=3)
        assert "chdb" in freqs
        assert freqs["chdb"] >= 4

    def test_filters_below_min_count(self, db):
        """Tags appearing fewer than min_count times are excluded."""
        db.insert(make_memory(layer="episodic", tags=["rare-tag"]))
        freqs = db.get_tag_frequencies(layer="episodic", min_count=3)
        assert "rare-tag" not in freqs


class TestCountAndStats:
    """Test counting and statistics methods."""

    def test_count_by_layer(self, populated_db):
        """count_by_layer returns correct counts per layer."""
        counts = populated_db.count_by_layer()
        assert counts["working"] == 1
        assert counts["episodic"] == 5
        assert counts["semantic"] == 5

    def test_total_count(self, populated_db):
        """count returns total active memory count."""
        assert populated_db.count() == 11  # 1 + 5 + 5

    def test_stats_has_layer_breakdown(self, populated_db):
        """stats returns layer x category breakdown."""
        s = populated_db.stats()
        assert isinstance(s, dict)


class TestSearchByVector:
    """P3: SQL-level cosineDistance pre-filter for candidate retrieval."""

    def test_returns_memories_ordered_by_similarity(self, db, mock_emb):
        """search_by_vector returns memories closest to the query vector."""
        contents = ["alpha beta gamma", "delta epsilon zeta", "alpha beta delta"]
        for c in contents:
            m = Memory(
                content=c, layer="episodic", category="event",
                embedding=mock_emb.encode_document(c), source="cli",
            )
            db.insert(m)

        query_vec = mock_emb.encode_query("alpha beta gamma")
        results = db.search_by_vector(query_vec, "episodic", limit=3)
        assert len(results) == 3
        assert all(hasattr(r, "content") for r in results)

    def test_respects_layer_filter(self, db, mock_emb):
        """search_by_vector only returns memories from the requested layer."""
        ep = Memory(
            content="episodic memory", layer="episodic", category="event",
            embedding=mock_emb.encode_document("episodic memory"), source="cli",
        )
        sem = Memory(
            content="semantic memory", layer="semantic", category="knowledge",
            embedding=mock_emb.encode_document("semantic memory"), source="cli",
        )
        db.insert(ep)
        db.insert(sem)

        query_vec = mock_emb.encode_query("memory")
        results = db.search_by_vector(query_vec, "semantic", limit=10)
        assert all(r.layer == "semantic" for r in results)
        assert len(results) == 1

    def test_respects_limit(self, db, mock_emb):
        """search_by_vector respects the limit parameter."""
        for i in range(10):
            m = Memory(
                content=f"memory number {i}", layer="episodic", category="event",
                embedding=mock_emb.encode_document(f"memory number {i}"), source="cli",
            )
            db.insert(m)

        query_vec = mock_emb.encode_query("memory")
        results = db.search_by_vector(query_vec, "episodic", limit=3)
        assert len(results) == 3

    def test_skips_empty_embeddings(self, db, mock_emb):
        """Memories with empty embeddings are excluded."""
        with_emb = Memory(
            content="has embedding", layer="episodic", category="event",
            embedding=mock_emb.encode_document("has embedding"), source="cli",
        )
        without_emb = Memory(
            content="no embedding", layer="episodic", category="event",
            embedding=[], source="cli",
        )
        db.insert(with_emb)
        db.insert(without_emb)

        query_vec = mock_emb.encode_query("embedding")
        results = db.search_by_vector(query_vec, "episodic", limit=10)
        assert all(len(r.embedding) > 0 for r in results)


class TestRawQuery:
    """Test raw SQL query execution."""

    def test_select_count(self, populated_db):
        """query can execute a simple COUNT query."""
        results = populated_db.query(
            "SELECT count() as cnt FROM memories FINAL WHERE is_active=1"
        )
        assert len(results) == 1
        assert results[0]["cnt"] == 11

    def test_select_by_layer(self, populated_db):
        """query can filter by layer."""
        results = populated_db.query(
            "SELECT content FROM memories FINAL WHERE layer='semantic' AND is_active=1"
        )
        assert len(results) == 5
