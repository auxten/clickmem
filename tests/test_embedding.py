"""Tests for EmbeddingEngine interface contract.

Tests against MockEmbeddingEngine to validate the interface,
plus semantic distance property tests that should also hold for real models.
"""

from __future__ import annotations

import math

import pytest

from tests.helpers.mock_embedding import MockEmbeddingEngine


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return dot / (norm_a * norm_b)


def _l2_norm(vec: list[float]) -> float:
    """Compute L2 norm of a vector."""
    return math.sqrt(sum(x * x for x in vec))


class TestMockEmbeddingBasics:
    """Test MockEmbeddingEngine fundamental behavior."""

    def test_dimension(self):
        """Engine reports the correct dimension."""
        eng = MockEmbeddingEngine(dimension=256)
        assert eng.dimension == 256

    def test_custom_dimension(self):
        """Engine supports custom dimensions."""
        eng = MockEmbeddingEngine(dimension=128)
        assert eng.dimension == 128

    def test_load(self):
        """load() succeeds without error."""
        eng = MockEmbeddingEngine()
        eng.load()

    def test_encode_query_returns_list(self):
        """encode_query returns a list of floats."""
        eng = MockEmbeddingEngine()
        vec = eng.encode_query("hello")
        assert isinstance(vec, list)
        assert all(isinstance(x, float) for x in vec)

    def test_encode_query_correct_dimension(self):
        """encode_query returns vector of configured dimension."""
        eng = MockEmbeddingEngine(dimension=256)
        vec = eng.encode_query("hello")
        assert len(vec) == 256

    def test_encode_document_correct_dimension(self):
        """encode_document returns vector of configured dimension."""
        eng = MockEmbeddingEngine(dimension=256)
        vec = eng.encode_document("hello")
        assert len(vec) == 256

    def test_encode_batch_returns_list_of_lists(self):
        """encode_batch returns a list of vectors."""
        eng = MockEmbeddingEngine()
        vecs = eng.encode_batch(["one", "two", "three"])
        assert len(vecs) == 3
        assert all(len(v) == eng.dimension for v in vecs)


class TestMockEmbeddingNormalization:
    """Test that vectors are L2-normalized."""

    def test_query_vector_unit_norm(self):
        """Query vectors have unit L2 norm."""
        eng = MockEmbeddingEngine()
        vec = eng.encode_query("normalize me")
        assert abs(_l2_norm(vec) - 1.0) < 1e-6

    def test_document_vector_unit_norm(self):
        """Document vectors have unit L2 norm."""
        eng = MockEmbeddingEngine()
        vec = eng.encode_document("normalize me")
        assert abs(_l2_norm(vec) - 1.0) < 1e-6

    def test_batch_vectors_unit_norm(self):
        """All batch vectors have unit L2 norm."""
        eng = MockEmbeddingEngine()
        vecs = eng.encode_batch(["a", "b", "c"])
        for v in vecs:
            assert abs(_l2_norm(v) - 1.0) < 1e-6


class TestMockEmbeddingDeterminism:
    """Test that identical inputs produce identical outputs."""

    def test_same_query_same_vector(self):
        """Same query text always produces the same vector."""
        eng = MockEmbeddingEngine()
        v1 = eng.encode_query("deterministic")
        v2 = eng.encode_query("deterministic")
        assert v1 == v2

    def test_same_document_same_vector(self):
        """Same document text always produces the same vector."""
        eng = MockEmbeddingEngine()
        v1 = eng.encode_document("deterministic")
        v2 = eng.encode_document("deterministic")
        assert v1 == v2

    def test_different_text_different_vector(self):
        """Different text produces different vectors."""
        eng = MockEmbeddingEngine()
        v1 = eng.encode_document("apple")
        v2 = eng.encode_document("banana")
        assert v1 != v2

    def test_query_vs_document_differ(self):
        """Query and document encoding of same text differ (simulates instruct prefix)."""
        eng = MockEmbeddingEngine()
        vq = eng.encode_query("same text")
        vd = eng.encode_document("same text")
        assert vq != vd


class TestEmbeddingInterfaceContract:
    """Test that the EmbeddingEngine public interface matches the spec."""

    def test_has_encode_query(self):
        """Engine exposes encode_query method."""
        eng = MockEmbeddingEngine()
        assert callable(eng.encode_query)

    def test_has_encode_document(self):
        """Engine exposes encode_document method."""
        eng = MockEmbeddingEngine()
        assert callable(eng.encode_document)

    def test_has_encode_batch(self):
        """Engine exposes encode_batch method."""
        eng = MockEmbeddingEngine()
        assert callable(eng.encode_batch)

    def test_has_dimension_property(self):
        """Engine exposes dimension property."""
        eng = MockEmbeddingEngine()
        assert isinstance(eng.dimension, int)

    def test_has_load(self):
        """Engine exposes load method."""
        eng = MockEmbeddingEngine()
        assert callable(eng.load)


class TestSemanticDistanceMock:
    """Test semantic distance properties on mock embeddings.

    These tests verify distance/similarity invariants that any well-behaved
    embedding engine should satisfy. They run on the mock engine and serve
    as a reference for real engine integration tests.
    """

    def test_identical_text_max_similarity(self):
        """Identical text should have cosine similarity close to 1.0."""
        eng = MockEmbeddingEngine()
        v = eng.encode_document("hello world")
        sim = _cosine_similarity(v, v)
        assert abs(sim - 1.0) < 1e-6

    def test_different_text_similarity_below_one(self):
        """Different text should have cosine similarity < 1.0."""
        eng = MockEmbeddingEngine()
        v1 = eng.encode_document("apple pie recipe")
        v2 = eng.encode_document("quantum mechanics lecture")
        sim = _cosine_similarity(v1, v2)
        assert sim < 1.0

    def test_similarity_is_symmetric(self):
        """cosine_similarity(a, b) == cosine_similarity(b, a)."""
        eng = MockEmbeddingEngine()
        v1 = eng.encode_document("text one")
        v2 = eng.encode_document("text two")
        assert abs(_cosine_similarity(v1, v2) - _cosine_similarity(v2, v1)) < 1e-10

    def test_batch_consistency_with_single(self):
        """Batch encoding should produce same vectors as individual encoding."""
        eng = MockEmbeddingEngine()
        texts = ["alpha", "beta", "gamma"]
        batch_vecs = eng.encode_batch(texts)
        single_vecs = [eng.encode_document(t) for t in texts]
        for bv, sv in zip(batch_vecs, single_vecs):
            assert bv == sv
