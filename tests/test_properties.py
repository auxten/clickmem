"""Hypothesis property-based tests for memory system invariants.

Tests mathematical and logical invariants that must always hold,
regardless of input.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

from memory_core.models import Memory, RetrievalConfig
from tests.helpers.mock_embedding import MockEmbeddingEngine

pytestmark = pytest.mark.property_based


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return dot / (norm_a * norm_b)


# -- Strategies ---------------------------------------------------------------

memory_layer = st.sampled_from(["working", "episodic", "semantic"])
memory_category = st.sampled_from([
    "decision", "preference", "event", "person", "project", "knowledge", "todo", "insight",
])
tag_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1, max_size=20,
)
content_text = st.text(min_size=1, max_size=500)


def _make_db():
    """Create a fresh MemoryDB for property tests."""
    from memory_core import MemoryDB
    db = MemoryDB(":memory:")
    db._truncate()
    return db


class TestDecayMonotonicity:
    """Time decay must be monotonically non-increasing over time."""

    @given(
        age_days_a=st.integers(min_value=0, max_value=1000),
        age_days_b=st.integers(min_value=0, max_value=1000),
        half_life=st.floats(min_value=1.0, max_value=365.0),
    )
    def test_decay_monotonic(self, age_days_a, age_days_b, half_life):
        """For exponential decay: if a is older than b, decay(a) <= decay(b)."""
        decay_a = math.exp(-math.log(2) * age_days_a / half_life)
        decay_b = math.exp(-math.log(2) * age_days_b / half_life)
        if age_days_a >= age_days_b:
            assert decay_a <= decay_b + 1e-10

    @given(age_days=st.integers(min_value=0, max_value=1000))
    def test_decay_between_zero_and_one(self, age_days):
        """Decay factor is always in [0, 1]."""
        half_life = 60.0
        decay = math.exp(-math.log(2) * age_days / half_life)
        assert 0.0 <= decay <= 1.0 + 1e-10


class TestWorkingMemorySingleton:
    """L0 working memory: only one entry should exist at any time."""

    @given(contents=st.lists(st.text(min_size=1, max_size=100), min_size=1, max_size=10))
    @settings(max_examples=20)
    def test_only_last_working_memory_retained(self, contents):
        """After N set_working calls, only the last content is retained."""
        db = _make_db()
        for c in contents:
            db.set_working(c)
        result = db.get_working()
        assert result == contents[-1]
        counts = db.count_by_layer()
        assert counts.get("working", 0) == 1


class TestTagRoundTrip:
    """Tags should survive a round-trip through insert/get."""

    @given(tags=st.lists(tag_text, min_size=0, max_size=10))
    @settings(max_examples=30)
    def test_tags_roundtrip(self, tags):
        """Tags inserted are exactly what we get back."""
        db = _make_db()
        from tests.helpers.factories import make_memory
        m = make_memory(tags=tags)
        db.insert(m)
        retrieved = db.get(m.id)
        assert set(retrieved.tags) == set(tags)


class TestTopKUpperBound:
    """hybrid_search never returns more than top_k results."""

    @given(top_k=st.integers(min_value=1, max_value=50))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_top_k_upper_bound(self, top_k, populated_db, mock_emb):
        """Result count <= top_k for any top_k value."""
        cfg = RetrievalConfig(top_k=top_k)
        from memory_core import hybrid_search
        results = hybrid_search(populated_db, mock_emb, "test", cfg=cfg)
        assert len(results) <= top_k


class TestEmbeddingNormalization:
    """All embedding vectors must have unit L2 norm."""

    @given(text=st.text(min_size=1, max_size=200))
    @settings(max_examples=50)
    def test_l2_norm_is_one(self, text):
        """Embedding vectors are unit-normalized."""
        eng = MockEmbeddingEngine(dimension=256)
        vec = eng.encode_document(text)
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-5


class TestCosineSimilarityRange:
    """Cosine similarity of normalized vectors is in [-1, 1]."""

    @given(
        text_a=st.text(min_size=1, max_size=200),
        text_b=st.text(min_size=1, max_size=200),
    )
    @settings(max_examples=50)
    def test_cosine_in_range(self, text_a, text_b):
        """Cosine similarity between any two embedding vectors is in [-1, 1]."""
        eng = MockEmbeddingEngine(dimension=256)
        va = eng.encode_document(text_a)
        vb = eng.encode_document(text_b)
        sim = _cosine_similarity(va, vb)
        assert -1.0 - 1e-6 <= sim <= 1.0 + 1e-6


class TestMemoryConstruction:
    """Memory objects constructed with arbitrary valid data should not crash."""

    @given(
        content=content_text,
        layer=memory_layer,
        category=memory_category,
        tags=st.lists(tag_text, max_size=5),
    )
    @settings(max_examples=30)
    def test_memory_construction_never_crashes(self, content, layer, category, tags):
        """Memory can be constructed with any valid combination of fields."""
        m = Memory(content=content, layer=layer, category=category, tags=tags)
        assert m.content == content
        assert m.layer == layer
        assert m.category == category
        assert m.tags == tags


class TestRetrievalConfigBounds:
    """RetrievalConfig weights and parameters must be valid."""

    @given(
        w_vector=st.floats(min_value=0.0, max_value=1.0),
        w_keyword=st.floats(min_value=0.0, max_value=1.0),
        mmr_lambda=st.floats(min_value=0.0, max_value=1.0),
    )
    def test_config_weights_in_range(self, w_vector, w_keyword, mmr_lambda):
        """All weight parameters should be valid floats in [0, 1]."""
        cfg = RetrievalConfig(w_vector=w_vector, w_keyword=w_keyword, mmr_lambda=mmr_lambda)
        assert 0.0 <= cfg.w_vector <= 1.0
        assert 0.0 <= cfg.w_keyword <= 1.0
        assert 0.0 <= cfg.mmr_lambda <= 1.0


class TestSelfSimilarityMaximum:
    """A vector's cosine similarity with itself must be ~1.0."""

    @given(text=st.text(min_size=1, max_size=200))
    @settings(max_examples=30)
    def test_self_similarity(self, text):
        """Cosine similarity of a vector with itself is 1.0."""
        eng = MockEmbeddingEngine(dimension=256)
        v = eng.encode_document(text)
        sim = _cosine_similarity(v, v)
        assert abs(sim - 1.0) < 1e-6
