"""Tests for the raw_transcripts table and raw-first ingestion flow."""

from __future__ import annotations

import json

import pytest

from memory_core.models import Memory


class TestRawTranscriptsTable:
    """Test raw_transcripts CRUD operations."""

    def test_insert_raw_returns_id(self, db):
        raw_id = db.insert_raw("session-1", "cursor", "Hello world")
        assert raw_id
        assert len(raw_id) == 36  # UUID format

    def test_get_raw(self, db):
        raw_id = db.insert_raw("session-1", "cursor", "Test content")
        row = db.get_raw(raw_id)
        assert row is not None
        assert row["id"] == raw_id
        assert row["session_id"] == "session-1"
        assert row["source"] == "cursor"
        assert row["content"] == "Test content"
        assert int(row["char_count"]) == len("Test content")
        assert int(row["is_processed"]) == 0

    def test_get_raw_not_found(self, db):
        assert db.get_raw("nonexistent-id") is None

    def test_list_unprocessed_raw(self, db):
        db.insert_raw("s1", "cursor", "First")
        db.insert_raw("s2", "cursor", "Second")
        db.insert_raw("s3", "cursor", "Third")

        unprocessed = db.list_unprocessed_raw()
        assert len(unprocessed) == 3

    def test_mark_raw_processed(self, db):
        raw_id = db.insert_raw("s1", "cursor", "Content")
        db.mark_raw_processed(raw_id)
        db.optimize()

        unprocessed = db.list_unprocessed_raw()
        assert len(unprocessed) == 0

        row = db.get_raw(raw_id)
        assert int(row["is_processed"]) == 1

    def test_count_raw(self, db):
        assert db.count_raw() == {"total": 0, "processed": 0, "unprocessed": 0}

        db.insert_raw("s1", "cursor", "A")
        db.insert_raw("s2", "cursor", "B")
        counts = db.count_raw()
        assert counts["total"] == 2
        assert counts["unprocessed"] == 2
        assert counts["processed"] == 0

        db.mark_raw_processed(db.list_unprocessed_raw()[0]["id"])
        db.optimize()
        counts = db.count_raw()
        assert counts["total"] == 2
        assert counts["processed"] == 1
        assert counts["unprocessed"] == 1

    def test_truncate_clears_raw(self, db):
        db.insert_raw("s1", "cursor", "Data")
        assert db.count_raw()["total"] == 1
        db._truncate()
        assert db.count_raw()["total"] == 0


class TestRawIdOnMemories:
    """Test that the raw_id field works on the memories table."""

    def test_memory_with_raw_id(self, db, mock_emb):
        raw_id = db.insert_raw("s1", "cursor", "Original conversation")
        m = Memory(
            content="Extracted fact",
            layer="episodic",
            category="event",
            embedding=mock_emb.encode_document("Extracted fact"),
            raw_id=raw_id,
        )
        db.insert(m)

        retrieved = db.get(m.id)
        assert retrieved is not None
        assert retrieved.raw_id == raw_id

    def test_memory_without_raw_id(self, db, mock_emb):
        m = Memory(
            content="No raw source",
            layer="semantic",
            embedding=mock_emb.encode_document("No raw source"),
        )
        db.insert(m)

        retrieved = db.get(m.id)
        assert retrieved is not None
        assert retrieved.raw_id is None


class TestIngestFlow:
    """Test the raw-first ingestion via LocalTransport."""

    def test_ingest_stores_raw_and_extracts(self, db, mock_emb, mock_llm):
        from memory_core.transport import LocalTransport
        import memory_core.llm as llm_mod

        t = LocalTransport()
        t._db = db
        t._emb = mock_emb
        llm_mod._local_engine = None
        llm_mod._local_engine_failed = True

        result = t.ingest("user: Hello\nassistant: Hi there", session_id="s1", source="cursor")

        assert "raw_id" in result
        assert "extracted_ids" in result
        assert len(result["extracted_ids"]) >= 1

        raw = db.get_raw(result["raw_id"])
        assert raw is not None
        assert raw["source"] == "cursor"

    def test_ingest_marks_raw_processed(self, db, mock_emb, mock_llm):
        from memory_core.transport import LocalTransport
        import memory_core.llm as llm_mod

        t = LocalTransport()
        t._db = db
        t._emb = mock_emb
        llm_mod._local_engine = None
        llm_mod._local_engine_failed = True

        result = t.ingest("Some conversation text", session_id="s1")

        db.optimize()
        raw = db.get_raw(result["raw_id"])
        assert int(raw["is_processed"]) == 1
