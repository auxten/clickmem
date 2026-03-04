"""Tests for Memory and RetrievalConfig data models.

Validates field defaults, types, and basic construction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from memory_core.models import Memory, RetrievalConfig
from tests.helpers.assertions import assert_valid_uuid


class TestMemoryDefaults:
    """Test Memory dataclass field defaults."""

    def test_default_layer_is_semantic(self):
        """Memory.layer defaults to 'semantic'."""
        m = Memory(content="hello")
        assert m.layer == "semantic"

    def test_default_category_is_knowledge(self):
        """Memory.category defaults to 'knowledge'."""
        m = Memory(content="hello")
        assert m.category == "knowledge"

    def test_default_tags_empty_list(self):
        """Memory.tags defaults to an empty list."""
        m = Memory(content="hello")
        assert m.tags == []

    def test_default_id_is_valid_uuid(self):
        """Memory.id is auto-generated as a valid UUID."""
        m = Memory(content="hello")
        assert_valid_uuid(m.id)

    def test_each_memory_gets_unique_id(self):
        """Two separate Memory instances get different IDs."""
        m1 = Memory(content="one")
        m2 = Memory(content="two")
        assert m1.id != m2.id

    def test_default_is_active_true(self):
        """Memory.is_active defaults to True."""
        m = Memory(content="hello")
        assert m.is_active is True

    def test_default_access_count_zero(self):
        """Memory.access_count defaults to 0."""
        m = Memory(content="hello")
        assert m.access_count == 0

    def test_default_source_is_agent(self):
        """Memory.source defaults to 'agent'."""
        m = Memory(content="hello")
        assert m.source == "agent"


class TestMemoryConstruction:
    """Test Memory construction with explicit values."""

    def test_all_fields_settable(self):
        """Can set all Memory fields via constructor."""
        now = datetime.now(timezone.utc)
        m = Memory(
            content="Test content",
            layer="episodic",
            category="decision",
            tags=["a", "b"],
            entities=["User"],
            embedding=[0.1, 0.2],
            session_id="s1",
            source="cli",
            id="custom-id",
            is_active=False,
            access_count=5,
            created_at=now,
            updated_at=now,
            accessed_at=now,
        )
        assert m.content == "Test content"
        assert m.layer == "episodic"
        assert m.category == "decision"
        assert m.tags == ["a", "b"]
        assert m.entities == ["User"]
        assert m.embedding == [0.1, 0.2]
        assert m.session_id == "s1"
        assert m.source == "cli"
        assert m.id == "custom-id"
        assert m.is_active is False
        assert m.access_count == 5

    def test_tags_are_independent_per_instance(self):
        """Default tags list should not be shared between instances."""
        m1 = Memory(content="one")
        m2 = Memory(content="two")
        m1.tags.append("x")
        assert "x" not in m2.tags


class TestRetrievalConfig:
    """Test RetrievalConfig dataclass."""

    def test_defaults(self):
        """RetrievalConfig has sensible defaults matching the spec."""
        cfg = RetrievalConfig()
        assert cfg.top_k == 15
        assert cfg.w_vector == 0.5
        assert cfg.w_keyword == 0.5
        assert cfg.decay_days == 60.0
        assert cfg.mmr_lambda == 0.7
        assert cfg.layer is None
        assert cfg.category is None
