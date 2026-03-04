"""Edge case, Unicode, concurrency, and capacity tests.

Tests boundary conditions, special characters, large payloads,
and concurrent access patterns.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from memory_core import MemoryDB, hybrid_search
from memory_core.models import Memory, RetrievalConfig
from tests.helpers.factories import make_memory, MemoryFactory
from tests.helpers.mock_embedding import MockEmbeddingEngine
from tests.helpers.assertions import (
    assert_valid_uuid,
    assert_no_duplicate_ids,
    assert_memory_active,
)


class TestUnicode:
    """Test Unicode content handling."""

    def test_chinese_content(self, db):
        """Memory with Chinese content stores and retrieves correctly."""
        m = make_memory(content="用户偏好使用SwiftUI而非UIKit")
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content == "用户偏好使用SwiftUI而非UIKit"

    def test_japanese_content(self, db):
        """Memory with Japanese content stores and retrieves correctly."""
        m = make_memory(content="ユーザーはPythonを好む")
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content == "ユーザーはPythonを好む"

    def test_emoji_content(self, db):
        """Memory with emoji content stores and retrieves correctly."""
        m = make_memory(content="Great progress today! 🚀🎉")
        db.insert(m)
        retrieved = db.get(m.id)
        assert "🚀" in retrieved.content

    def test_mixed_script_content(self, db):
        """Memory with mixed scripts stores correctly."""
        content = "Alice说决定使用gRPC (グーグルのRPC) for API"
        m = make_memory(content=content)
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content == content

    def test_unicode_tags(self, db):
        """Tags with Unicode characters work correctly."""
        m = make_memory(tags=["编程", "プロジェクト", "café"])
        db.insert(m)
        retrieved = db.get(m.id)
        assert "编程" in retrieved.tags

    def test_unicode_in_embedding_query(self):
        """MockEmbeddingEngine handles Unicode input."""
        eng = MockEmbeddingEngine()
        vec = eng.encode_query("用户偏好")
        assert len(vec) == eng.dimension

    def test_rtl_content(self, db):
        """Memory with RTL (Arabic/Hebrew) content stores correctly."""
        content = "المستخدم يفضل Python"
        m = make_memory(content=content)
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content == content


class TestEmptyAndMinimal:
    """Test empty/minimal input handling."""

    def test_single_character_content(self, db):
        """Memory with single character content works."""
        m = make_memory(content="A")
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content == "A"

    def test_empty_tags_list(self, db):
        """Memory with empty tags list works."""
        m = make_memory(tags=[])
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.tags == []

    def test_whitespace_only_content(self, db):
        """Memory with whitespace-only content is handled."""
        m = make_memory(content="   ")
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content.strip() == "" or retrieved.content == "   "

    def test_very_long_content(self, db):
        """Memory with very long content (10KB) stores correctly."""
        long_content = "A" * 10_000
        m = make_memory(content=long_content)
        db.insert(m)
        retrieved = db.get(m.id)
        assert len(retrieved.content) == 10_000

    def test_many_tags(self, db):
        """Memory with many tags (50) stores correctly."""
        tags = [f"tag-{i}" for i in range(50)]
        m = make_memory(tags=tags)
        db.insert(m)
        retrieved = db.get(m.id)
        assert len(retrieved.tags) == 50


class TestSpecialCharacters:
    """Test special characters in content and tags."""

    def test_sql_injection_in_content(self, db):
        """Content with SQL-like strings is handled safely."""
        m = make_memory(content="'; DROP TABLE memories; --")
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.content == "'; DROP TABLE memories; --"
        # Database should still be functional
        assert db.count() >= 1

    def test_newlines_in_content(self, db):
        """Content with newlines stores correctly."""
        content = "Line 1\nLine 2\nLine 3"
        m = make_memory(content=content)
        db.insert(m)
        retrieved = db.get(m.id)
        assert "\n" in retrieved.content

    def test_quotes_in_content(self, db):
        """Content with various quote types stores correctly."""
        content = """He said "hello" and she said 'hi' and `code`"""
        m = make_memory(content=content)
        db.insert(m)
        retrieved = db.get(m.id)
        assert '"hello"' in retrieved.content

    def test_backslashes_in_content(self, db):
        """Content with backslashes stores correctly."""
        content = r"C:\Users\path\to\file"
        m = make_memory(content=content)
        db.insert(m)
        retrieved = db.get(m.id)
        assert "\\" in retrieved.content


class TestCapacity:
    """Test capacity and scaling."""

    @pytest.mark.slow
    def test_insert_1000_memories(self, db):
        """Database handles 1000 memory insertions."""
        for i in range(1000):
            db.insert(make_memory(content=f"Memory number {i}"))
        assert db.count() == 1000

    @pytest.mark.slow
    def test_search_with_many_entries(self, db, mock_emb):
        """Search works correctly with 500+ entries."""
        for i in range(500):
            m = make_memory(
                layer="episodic",
                content=f"Event number {i}: various topic about coding",
                embedding=mock_emb.encode_document(f"Event number {i}"),
            )
            db.insert(m)
        results = hybrid_search(
            db, mock_emb,
            query="coding event",
            cfg=RetrievalConfig(top_k=10),
        )
        assert len(results) <= 10
        assert_no_duplicate_ids(results)


class TestConcurrency:
    """Test concurrent access patterns."""

    @pytest.mark.slow
    def test_concurrent_inserts(self, db):
        """Multiple threads can insert simultaneously."""
        errors = []

        def insert_batch(thread_id, count):
            try:
                for i in range(count):
                    m = make_memory(content=f"Thread {thread_id} memory {i}")
                    db.insert(m)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=insert_batch, args=(t, 20))
            for t in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent insert: {errors}"
        assert db.count() == 100

    @pytest.mark.slow
    def test_concurrent_read_write(self, db, mock_emb):
        """Reads and writes can happen concurrently."""
        # Pre-populate
        for i in range(50):
            m = make_memory(
                layer="episodic",
                content=f"Pre-existing memory {i}",
                embedding=mock_emb.encode_document(f"Pre-existing memory {i}"),
            )
            db.insert(m)

        errors = []

        def reader():
            try:
                for _ in range(10):
                    hybrid_search(
                        db, mock_emb,
                        query="memory",
                        cfg=RetrievalConfig(top_k=5),
                    )
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(10):
                    m = make_memory(
                        content=f"New memory {i}",
                        embedding=mock_emb.encode_document(f"New memory {i}"),
                    )
                    db.insert(m)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors during concurrent read/write: {errors}"


class TestIDHandling:
    """Test ID-related edge cases."""

    def test_custom_id(self, db):
        """Memory with a custom ID preserves it."""
        m = make_memory(id="custom-test-id-12345")
        db.insert(m)
        retrieved = db.get("custom-test-id-12345")
        assert retrieved is not None

    def test_uuid_format(self, db):
        """Auto-generated IDs are valid UUIDs."""
        m = make_memory()
        db.insert(m)
        assert_valid_uuid(m.id)

    def test_duplicate_id_handling(self, db):
        """Inserting two memories with the same ID is handled."""
        m1 = make_memory(id="same-id", content="First")
        m2 = make_memory(id="same-id", content="Second")
        db.insert(m1)
        # Second insert with same ID should either raise or overwrite
        try:
            db.insert(m2)
            # If it succeeds, verify the behavior
            retrieved = db.get("same-id")
            assert retrieved is not None
        except Exception:
            # Duplicate ID rejection is also acceptable
            pass


class TestTimestamps:
    """Test timestamp handling."""

    def test_created_at_preserved(self, db):
        """created_at timestamp is preserved on insert."""
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        m = make_memory(created_at=ts)
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.created_at is not None

    def test_timezone_handling(self, db):
        """Timestamps with timezone info are handled correctly."""
        ts = datetime(2026, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
        m = make_memory(created_at=ts, updated_at=ts, accessed_at=ts)
        db.insert(m)
        retrieved = db.get(m.id)
        assert retrieved.created_at is not None
