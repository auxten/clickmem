"""Semantic distance tests using a real local embedding model.

These tests verify that the embedding model produces vectors with
meaningful semantic relationships — similar texts should be closer
in vector space than unrelated texts.

Requires: pip install sentence-transformers
Model: Qwen/Qwen3-Embedding-0.6B (~600M params, auto-downloaded on first run)

Run with: pytest tests/test_semantic_distance.py -m semantic -v
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.semantic

# ---- Fixtures ----------------------------------------------------------------

@pytest.fixture(scope="module")
def real_emb():
    """Load the real embedding model once per module (expensive)."""
    try:
        from tests.helpers.real_embedding import RealEmbeddingEngine
    except ImportError:
        pytest.skip("sentence-transformers not installed")
    engine = RealEmbeddingEngine()  # defaults to Qwen/Qwen3-Embedding-0.6B, 256d
    engine.load()
    return engine


# ---- Helpers -----------------------------------------------------------------

def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-10 or nb < 1e-10:
        return 0.0
    return dot / (na * nb)


# ---- Tests -------------------------------------------------------------------

class TestSemanticSimilarityBasics:
    """Verify that semantically similar texts have higher cosine similarity."""

    def test_identical_text_similarity_one(self, real_emb):
        """Identical text should have cosine similarity ~1.0."""
        v = real_emb.encode_document("The cat sat on the mat")
        sim = cosine_sim(v, v)
        assert abs(sim - 1.0) < 1e-5

    def test_paraphrase_high_similarity(self, real_emb):
        """Paraphrases should have high cosine similarity (> 0.7)."""
        v1 = real_emb.encode_document("The user prefers Python for backend development")
        v2 = real_emb.encode_document("Python is the user's preferred language for server-side code")
        sim = cosine_sim(v1, v2)
        assert sim > 0.7, f"Paraphrase similarity too low: {sim:.3f}"

    def test_related_topic_moderate_similarity(self, real_emb):
        """Related but distinct topics should have moderate similarity."""
        v1 = real_emb.encode_document("Python programming language")
        v2 = real_emb.encode_document("JavaScript programming language")
        sim = cosine_sim(v1, v2)
        assert 0.3 < sim < 0.95, f"Related topic similarity unexpected: {sim:.3f}"

    def test_unrelated_topics_low_similarity(self, real_emb):
        """Completely unrelated topics should have low similarity."""
        v1 = real_emb.encode_document("Machine learning model training with gradient descent")
        v2 = real_emb.encode_document("Italian pasta recipe with tomato sauce and basil")
        sim = cosine_sim(v1, v2)
        assert sim < 0.4, f"Unrelated similarity too high: {sim:.3f}"


class TestSemanticTriangleInequality:
    """Test that similarity relationships follow semantic logic.

    If A is more similar to B than to C, and the texts confirm this
    intuitively, the model should reflect it.
    """

    def test_closer_to_synonym_than_antonym(self, real_emb):
        """'happy' should be closer to 'joyful' than to 'sad'."""
        v_happy = real_emb.encode_document("The user is happy with the result")
        v_joyful = real_emb.encode_document("The user is joyful about the outcome")
        v_sad = real_emb.encode_document("The user is sad about the failure")
        sim_synonym = cosine_sim(v_happy, v_joyful)
        sim_antonym = cosine_sim(v_happy, v_sad)
        assert sim_synonym > sim_antonym, (
            f"Synonym sim ({sim_synonym:.3f}) should exceed antonym sim ({sim_antonym:.3f})"
        )

    def test_same_domain_closer_than_cross_domain(self, real_emb):
        """Two programming topics should be closer than a programming + cooking topic."""
        v_python = real_emb.encode_document("Writing a REST API in Python with FastAPI")
        v_rust = real_emb.encode_document("Building a web server in Rust with Actix")
        v_cooking = real_emb.encode_document("Baking sourdough bread from scratch")
        sim_same_domain = cosine_sim(v_python, v_rust)
        sim_cross_domain = cosine_sim(v_python, v_cooking)
        assert sim_same_domain > sim_cross_domain, (
            f"Same domain sim ({sim_same_domain:.3f}) should exceed "
            f"cross domain sim ({sim_cross_domain:.3f})"
        )

    def test_person_reference_consistency(self, real_emb):
        """Mentions of the same person should be closer than different people."""
        v_alice1 = real_emb.encode_document("Meeting with Alice about the database design")
        v_alice2 = real_emb.encode_document("Alice reviewed the pull request for the API")
        v_bob = real_emb.encode_document("Bob presented the quarterly sales report")
        sim_same_person = cosine_sim(v_alice1, v_alice2)
        sim_diff_person = cosine_sim(v_alice1, v_bob)
        assert sim_same_person > sim_diff_person, (
            f"Same person sim ({sim_same_person:.3f}) should exceed "
            f"different person sim ({sim_diff_person:.3f})"
        )


class TestMemoryLayerSemantics:
    """Test semantic distance in the context of memory layer content."""

    def test_preference_similarity(self, real_emb):
        """Two preference memories about the same topic should be similar."""
        v1 = real_emb.encode_document("User prefers dark mode in all IDEs")
        v2 = real_emb.encode_document("User likes dark themes for code editors")
        sim = cosine_sim(v1, v2)
        assert sim > 0.5, f"Preference similarity too low: {sim:.3f}"

    def test_decision_vs_preference(self, real_emb):
        """A decision about a tool should relate to preference for that tool."""
        v_decision = real_emb.encode_document("Decided to use PostgreSQL for the project database")
        v_preference = real_emb.encode_document("User prefers PostgreSQL over MySQL")
        v_unrelated = real_emb.encode_document("Scheduled team lunch for Friday")
        sim_related = cosine_sim(v_decision, v_preference)
        sim_unrelated = cosine_sim(v_decision, v_unrelated)
        assert sim_related > sim_unrelated, (
            f"Related sim ({sim_related:.3f}) should exceed "
            f"unrelated sim ({sim_unrelated:.3f})"
        )

    def test_working_memory_focus(self, real_emb):
        """Working memory about HNSW should match query about HNSW."""
        v_working = real_emb.encode_document(
            "User is debugging HNSW index configuration, follow up on bf16 quantization"
        )
        v_query = real_emb.encode_query("HNSW index performance and configuration")
        v_unrelated = real_emb.encode_query("How to cook pasta carbonara")
        sim_relevant = cosine_sim(v_working, v_query)
        sim_unrelated = cosine_sim(v_working, v_unrelated)
        assert sim_relevant > sim_unrelated, (
            f"Relevant sim ({sim_relevant:.3f}) should exceed "
            f"unrelated sim ({sim_unrelated:.3f})"
        )


class TestQueryDocumentAsymmetry:
    """Test behavior differences between query and document encoding."""

    def test_query_and_doc_same_text_high_sim(self, real_emb):
        """Query and document encoding of same text should still be very similar."""
        text = "machine learning model training"
        vq = real_emb.encode_query(text)
        vd = real_emb.encode_document(text)
        sim = cosine_sim(vq, vd)
        # Qwen3 uses prompt_name="query" which adds an instruction prefix,
        # so query/doc similarity for same text is ~0.8 rather than ~1.0
        assert sim > 0.7, f"Query/doc same text sim too low: {sim:.3f}"


class TestBatchConsistency:
    """Test that batch encoding matches individual encoding."""

    def test_batch_matches_single(self, real_emb):
        """Batch encoding should produce same vectors as individual encoding."""
        texts = [
            "Python web development",
            "Machine learning with PyTorch",
            "Database optimization techniques",
        ]
        batch_vecs = real_emb.encode_batch(texts)
        single_vecs = [real_emb.encode_document(t) for t in texts]
        for bv, sv in zip(batch_vecs, single_vecs):
            sim = cosine_sim(bv, sv)
            assert abs(sim - 1.0) < 1e-3, f"Batch/single mismatch: sim={sim:.6f}"


class TestEmbeddingNormalization:
    """Test that real model vectors are properly normalized."""

    def test_vectors_are_unit_norm(self, real_emb):
        """All vectors should have L2 norm ~1.0."""
        texts = [
            "Short",
            "A medium length sentence about programming",
            "A much longer paragraph that discusses various topics including machine learning, "
            "software engineering, database design, and project management in great detail "
            "with many different concepts and ideas.",
        ]
        for text in texts:
            vec = real_emb.encode_document(text)
            norm = math.sqrt(sum(x * x for x in vec))
            # MRL truncation to 256d from 1024d can cause slight norm drift
            assert abs(norm - 1.0) < 0.01, f"Norm is {norm:.6f} for text: {text[:30]}..."


class TestDimensionTruncation:
    """Test dimension truncation (MRL) behavior."""

    def test_truncated_preserves_similarity_ranking(self):
        """Truncating from 1024d to 256d should preserve relative similarity rankings."""
        try:
            from tests.helpers.real_embedding import RealEmbeddingEngine
        except ImportError:
            pytest.skip("sentence-transformers not installed")

        # Full dimension engine (1024d native)
        full_eng = RealEmbeddingEngine(dimension=1024)
        full_eng.load()

        # Truncated engine (256d via MRL)
        trunc_eng = RealEmbeddingEngine(dimension=256)
        trunc_eng.load()

        text_a = "Python web development with Flask"
        text_b = "Python API development with Django"  # related
        text_c = "Gardening tips for spring flowers"  # unrelated

        # Full dimension
        va_full = full_eng.encode_document(text_a)
        vb_full = full_eng.encode_document(text_b)
        vc_full = full_eng.encode_document(text_c)
        sim_ab_full = cosine_sim(va_full, vb_full)
        sim_ac_full = cosine_sim(va_full, vc_full)

        # Truncated
        va_trunc = trunc_eng.encode_document(text_a)
        vb_trunc = trunc_eng.encode_document(text_b)
        vc_trunc = trunc_eng.encode_document(text_c)
        sim_ab_trunc = cosine_sim(va_trunc, vb_trunc)
        sim_ac_trunc = cosine_sim(va_trunc, vc_trunc)

        # The relative ranking should be preserved
        assert (sim_ab_full > sim_ac_full) == (sim_ab_trunc > sim_ac_trunc), (
            f"Ranking mismatch: full({sim_ab_full:.3f} > {sim_ac_full:.3f}) vs "
            f"trunc({sim_ab_trunc:.3f} > {sim_ac_trunc:.3f})"
        )
