"""Tests for MemoryExtractor — conversation memory extraction logic.

Tests extract() and emergency_flush() with MockLLMComplete.
"""

from __future__ import annotations

import json

import pytest

from memory_core import MemoryExtractor, MemoryDB
from memory_core.models import Memory
from tests.helpers.mock_llm import MockLLMComplete
from tests.helpers.factories import make_memory
from tests.helpers.assertions import assert_valid_uuid


def _sample_messages() -> list[dict]:
    """Sample conversation messages for extraction."""
    return [
        {"role": "user", "content": "Let's use Python for the core module."},
        {"role": "assistant", "content": "Good choice. I'll set up the project with Python and a thin JS shell for the plugin."},
        {"role": "user", "content": "I prefer functional programming style."},
        {"role": "assistant", "content": "Noted. I'll keep the code functional where possible."},
    ]


class TestExtractBasics:
    """Test basic extraction behavior."""

    def test_extract_returns_list_of_ids(self, db, mock_emb, mock_llm):
        """extract() returns a list of memory IDs."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(
            messages=_sample_messages(),
            llm_complete=mock_llm,
            session_id="sess-1",
        )
        assert isinstance(ids, list)
        for mid in ids:
            assert_valid_uuid(mid)

    def test_extract_creates_memories_in_db(self, db, mock_emb, mock_llm):
        """Extracted memories are stored in the database."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(
            messages=_sample_messages(),
            llm_complete=mock_llm,
            session_id="sess-1",
        )
        for mid in ids:
            m = db.get(mid)
            assert m is not None
            assert m.content != ""

    def test_extract_calls_llm(self, db, mock_emb, mock_llm):
        """extract() invokes the LLM at least once."""
        extractor = MemoryExtractor(db, mock_emb)
        extractor.extract(
            messages=_sample_messages(),
            llm_complete=mock_llm,
            session_id="sess-1",
        )
        assert mock_llm.call_count >= 1

    def test_extract_sets_session_id(self, db, mock_emb, mock_llm):
        """Extracted memories carry the session_id."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(
            messages=_sample_messages(),
            llm_complete=mock_llm,
            session_id="sess-42",
        )
        for mid in ids:
            m = db.get(mid)
            assert m.session_id == "sess-42"

    def test_extract_sets_embedding(self, db, mock_emb, mock_llm):
        """Extracted memories have embedding vectors."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(
            messages=_sample_messages(),
            llm_complete=mock_llm,
        )
        for mid in ids:
            m = db.get(mid)
            assert m.embedding is not None
            assert len(m.embedding) == mock_emb.dimension

    def test_extract_assigns_valid_layer(self, db, mock_emb, mock_llm):
        """Extracted memories are assigned to valid layers."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(
            messages=_sample_messages(),
            llm_complete=mock_llm,
        )
        valid_layers = {"working", "episodic", "semantic"}
        for mid in ids:
            m = db.get(mid)
            assert m.layer in valid_layers

    def test_extract_assigns_source_agent(self, db, mock_emb, mock_llm):
        """Extracted memories have source='agent'."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(
            messages=_sample_messages(),
            llm_complete=mock_llm,
        )
        for mid in ids:
            m = db.get(mid)
            assert m.source == "agent"


class TestExtractEmpty:
    """Test extraction edge cases."""

    def test_empty_messages_returns_empty(self, db, mock_emb, mock_llm):
        """extract() with empty messages returns no IDs."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(messages=[], llm_complete=mock_llm)
        assert ids == []

    def test_single_message(self, db, mock_emb, mock_llm):
        """extract() handles a single message."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.extract(
            messages=[{"role": "user", "content": "Hello"}],
            llm_complete=mock_llm,
        )
        assert isinstance(ids, list)


class TestEmergencyFlush:
    """Test emergency_flush before compaction."""

    def test_emergency_flush_returns_ids(self, db, mock_emb, mock_llm):
        """emergency_flush returns a list of memory IDs."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.emergency_flush(
            context="User was debugging HNSW performance issues",
            llm_complete=mock_llm,
        )
        assert isinstance(ids, list)
        for mid in ids:
            assert_valid_uuid(mid)

    def test_emergency_flush_creates_episodic(self, db, mock_emb, mock_llm):
        """emergency_flush creates episodic memories."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.emergency_flush(
            context="User was debugging HNSW performance issues",
            llm_complete=mock_llm,
        )
        for mid in ids:
            m = db.get(mid)
            assert m is not None
            assert m.layer == "episodic"

    def test_emergency_flush_source_is_compaction(self, db, mock_emb, mock_llm):
        """emergency_flush sets source='compaction_flush'."""
        extractor = MemoryExtractor(db, mock_emb)
        ids = extractor.emergency_flush(
            context="Critical context",
            llm_complete=mock_llm,
        )
        for mid in ids:
            m = db.get(mid)
            assert m.source == "compaction_flush"

    def test_emergency_flush_calls_llm_with_emergency_keyword(self, db, mock_emb, mock_llm):
        """emergency_flush passes prompt with 'emergency' keyword to LLM."""
        extractor = MemoryExtractor(db, mock_emb)
        extractor.emergency_flush(context="context", llm_complete=mock_llm)
        assert mock_llm.call_count >= 1
        # The LLM should receive a prompt containing emergency-related content
        assert any("emergency" in c.prompt.lower() or "compaction" in c.prompt.lower()
                    for c in mock_llm.calls)
