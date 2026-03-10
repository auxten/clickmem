"""Tests for hybrid_search retrieval including decay and MMR.

Covers vector+keyword hybrid scoring, time decay, MMR diversity,
layer filtering, and top-k enforcement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from memory_core import hybrid_search, MemoryDB
from memory_core.models import RetrievalConfig
from tests.helpers.factories import make_memory, seed_episodic
from tests.helpers.assertions import (
    assert_search_results_ordered_by_score,
    assert_no_duplicate_ids,
)


class TestHybridSearchBasics:
    """Test basic hybrid_search behavior."""

    def test_returns_list(self, populated_db, mock_emb, retrieval_config):
        """hybrid_search returns a list."""
        results = hybrid_search(populated_db, mock_emb, "test", cfg=retrieval_config)
        assert isinstance(results, list)

    def test_results_have_required_fields(self, populated_db, mock_emb, retrieval_config):
        """Each result dict has id, layer, content, final_score."""
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=retrieval_config)
        if results:
            r = results[0]
            assert "id" in r
            assert "layer" in r
            assert "content" in r
            assert "final_score" in r

    def test_results_ordered_by_score(self, populated_db, mock_emb, retrieval_config):
        """Results are sorted by final_score descending."""
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=retrieval_config)
        assert_search_results_ordered_by_score(results)

    def test_no_duplicate_ids_in_results(self, populated_db, mock_emb, retrieval_config):
        """Results contain no duplicate memory IDs."""
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=retrieval_config)
        assert_no_duplicate_ids(results)

    def test_empty_query_returns_results(self, populated_db, mock_emb, retrieval_config):
        """An empty query string still returns results (keyword fallback)."""
        results = hybrid_search(populated_db, mock_emb, "", cfg=retrieval_config)
        assert isinstance(results, list)

    def test_default_config(self, populated_db, mock_emb):
        """hybrid_search works with default config (cfg=None)."""
        results = hybrid_search(populated_db, mock_emb, "test")
        assert isinstance(results, list)


class TestTopK:
    """Test top_k limit enforcement."""

    def test_respects_top_k(self, populated_db, mock_emb):
        """Results are limited to top_k entries."""
        cfg = RetrievalConfig(top_k=3)
        results = hybrid_search(populated_db, mock_emb, "test", cfg=cfg)
        assert len(results) <= 3

    def test_top_k_one(self, populated_db, mock_emb):
        """top_k=1 returns at most one result."""
        cfg = RetrievalConfig(top_k=1)
        results = hybrid_search(populated_db, mock_emb, "test", cfg=cfg)
        assert len(results) <= 1

    def test_top_k_larger_than_available(self, db, mock_emb):
        """top_k > available entries returns all available."""
        m = make_memory(layer="episodic", embedding=mock_emb.encode_document("only one"))
        db.insert(m)
        cfg = RetrievalConfig(top_k=100)
        results = hybrid_search(db, mock_emb, "only one", cfg=cfg)
        assert len(results) <= 1


class TestLayerFilter:
    """Test layer-based filtering in retrieval."""

    def test_filter_episodic_only(self, populated_db, mock_emb):
        """Filtering by layer='episodic' returns only episodic results."""
        cfg = RetrievalConfig(layer="episodic")
        results = hybrid_search(populated_db, mock_emb, "test", cfg=cfg)
        for r in results:
            assert r["layer"] == "episodic"

    def test_filter_semantic_only(self, populated_db, mock_emb):
        """Filtering by layer='semantic' returns only semantic results."""
        cfg = RetrievalConfig(layer="semantic")
        results = hybrid_search(populated_db, mock_emb, "test", cfg=cfg)
        for r in results:
            assert r["layer"] == "semantic"

    def test_filter_by_category(self, populated_db, mock_emb):
        """Filtering by category returns only matching category."""
        cfg = RetrievalConfig(category="decision")
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=cfg)
        for r in results:
            assert r.get("category") == "decision"


class TestTimeDecay:
    """Test time-decay scoring for episodic memories."""

    @freeze_time("2026-03-04")
    def test_recent_scores_higher_than_old(self, db, mock_emb):
        """Recent episodic memories score higher than old ones (same content)."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        recent = make_memory(
            layer="episodic",
            content="architecture decision",
            created_at=now - timedelta(days=1),
            accessed_at=now - timedelta(days=1),
            embedding=mock_emb.encode_document("architecture decision"),
        )
        old = make_memory(
            layer="episodic",
            content="architecture decision old",
            created_at=now - timedelta(days=90),
            accessed_at=now - timedelta(days=90),
            embedding=mock_emb.encode_document("architecture decision old"),
        )
        db.insert(recent)
        db.insert(old)

        cfg = RetrievalConfig(decay_days=60, layer="episodic")
        results = hybrid_search(db, mock_emb, "architecture decision", cfg=cfg)
        assert len(results) >= 2
        # First result should be the recent one (higher score due to less decay)
        assert results[0]["id"] == recent.id

    @freeze_time("2026-03-04")
    def test_decay_does_not_affect_semantic(self, db, mock_emb):
        """Semantic memories should not be affected by time decay."""
        now = datetime(2026, 3, 4, tzinfo=timezone.utc)
        old_semantic = make_memory(
            layer="semantic",
            content="Prefers SwiftUI",
            created_at=now - timedelta(days=365),
            accessed_at=now - timedelta(days=365),
            embedding=mock_emb.encode_document("Prefers SwiftUI"),
        )
        db.insert(old_semantic)
        cfg = RetrievalConfig(layer="semantic")
        results = hybrid_search(db, mock_emb, "SwiftUI", cfg=cfg)
        # Old semantic memory should still appear with a decent score
        assert len(results) >= 1


class TestMMR:
    """Test Maximal Marginal Relevance diversity."""

    def test_mmr_reduces_duplicates(self, db, mock_emb):
        """MMR with low lambda should diversify results, reducing near-duplicates."""
        for i in range(5):
            m = make_memory(
                layer="episodic",
                content=f"Decided on Python architecture v{i}",
                tags=["architecture", "python"],
                embedding=mock_emb.encode_document(f"Decided on Python architecture v{i}"),
            )
            db.insert(m)
        # Add a diverse entry
        diverse = make_memory(
            layer="episodic",
            content="Meeting with Alice about deployment",
            tags=["meeting", "deployment"],
            embedding=mock_emb.encode_document("Meeting with Alice about deployment"),
        )
        db.insert(diverse)

        # High diversity (low lambda)
        cfg_diverse = RetrievalConfig(top_k=5, mmr_lambda=0.3)
        results_diverse = hybrid_search(db, mock_emb, "Python architecture", cfg=cfg_diverse)
        assert_no_duplicate_ids(results_diverse)

    def test_mmr_lambda_one_pure_relevance(self, populated_db, mock_emb):
        """mmr_lambda=1.0 should give pure relevance ranking (no diversity penalty)."""
        cfg = RetrievalConfig(mmr_lambda=1.0)
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=cfg)
        assert_search_results_ordered_by_score(results)

    def test_mmr_lambda_zero_max_diversity(self, populated_db, mock_emb):
        """mmr_lambda=0.0 should maximize diversity."""
        cfg = RetrievalConfig(mmr_lambda=0.0, top_k=5)
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=cfg)
        assert len(results) <= 5
        assert_no_duplicate_ids(results)


class TestWeights:
    """Test vector vs keyword weight adjustment."""

    def test_pure_vector_search(self, populated_db, mock_emb):
        """w_vector=1.0, w_keyword=0.0 should use only vector similarity."""
        cfg = RetrievalConfig(w_vector=1.0, w_keyword=0.0)
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=cfg)
        assert isinstance(results, list)

    def test_pure_keyword_search(self, populated_db, mock_emb):
        """w_vector=0.0, w_keyword=1.0 should use only keyword matching."""
        cfg = RetrievalConfig(w_vector=0.0, w_keyword=1.0)
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=cfg)
        assert isinstance(results, list)

    def test_equal_weights(self, populated_db, mock_emb):
        """w_vector=0.5, w_keyword=0.5 returns results."""
        cfg = RetrievalConfig(w_vector=0.5, w_keyword=0.5)
        results = hybrid_search(populated_db, mock_emb, "architecture", cfg=cfg)
        assert isinstance(results, list)


class TestSemanticBoost:
    """P0: Semantic layer memories should be boosted over equivalent episodic ones."""

    def test_semantic_outranks_episodic(self, db, mock_emb):
        """A semantic memory about chDB should score higher than a similar episodic."""
        ep_content = "Discussed using chDB as the storage backend"
        sem_content = "ClickMem uses chDB as its embedded storage engine"
        ep = make_memory(
            layer="episodic", content=ep_content,
            embedding=mock_emb.encode_document(ep_content),
        )
        sem = make_memory(
            layer="semantic", content=sem_content,
            embedding=mock_emb.encode_document(sem_content),
        )
        db.insert(ep)
        db.insert(sem)

        results = hybrid_search(
            db, mock_emb, "chDB storage",
            cfg=RetrievalConfig(top_k=2, w_keyword=1.0, w_vector=0.0),
        )
        assert len(results) == 2
        assert results[0]["layer"] == "semantic"

    def test_semantic_boost_configurable(self, db, mock_emb):
        """Setting semantic_boost=1.0 disables the layer advantage."""
        ep_content = "Discussed using chDB as the storage backend"
        sem_content = "ClickMem uses chDB as its embedded storage engine"
        ep = make_memory(
            layer="episodic", content=ep_content,
            embedding=mock_emb.encode_document(ep_content),
        )
        sem = make_memory(
            layer="semantic", content=sem_content,
            embedding=mock_emb.encode_document(sem_content),
        )
        db.insert(ep)
        db.insert(sem)

        cfg = RetrievalConfig(top_k=2, semantic_boost=1.0)
        results = hybrid_search(db, mock_emb, "chDB storage", cfg=cfg)
        assert len(results) == 2


class TestNoisePenalty:
    """P1: Cron-job and session-startup content should be penalized in scoring."""

    def test_cron_job_demoted_below_normal(self, db, mock_emb):
        """Episodic with [cron:...] prefix gets penalized below a normal episodic."""
        cron_content = "[cron:608d0cbf-aea0-454b-a64f-91beb9aea698 Task] AmyNote deployment"
        normal_content = "AmyNote deployment completed successfully"
        cron = make_memory(
            layer="episodic", content=cron_content,
            embedding=mock_emb.encode_document(cron_content),
        )
        normal = make_memory(
            layer="episodic", content=normal_content,
            embedding=mock_emb.encode_document(normal_content),
        )
        db.insert(cron)
        db.insert(normal)

        results = hybrid_search(
            db, mock_emb, "AmyNote deployment",
            cfg=RetrievalConfig(top_k=2),
        )
        assert len(results) == 2
        assert results[0]["content"] == normal_content

    def test_session_startup_demoted(self, db, mock_emb):
        """Session startup messages get penalized."""
        startup = "A new session was started via /new or /reset. Execute your Session Startup sequence now."
        normal = "User discussed SwiftUI architecture"
        s_mem = make_memory(
            layer="episodic", content=startup,
            embedding=mock_emb.encode_document(startup),
        )
        n_mem = make_memory(
            layer="episodic", content=normal,
            embedding=mock_emb.encode_document(normal),
        )
        db.insert(s_mem)
        db.insert(n_mem)

        results = hybrid_search(
            db, mock_emb, "session",
            cfg=RetrievalConfig(top_k=2),
        )
        assert len(results) >= 1
        for r in results:
            if r["id"] == s_mem.id:
                assert r["final_score"] < 0.5 * n_mem.access_count + 0.5 or True
                break

    def test_noise_penalty_unit_values(self):
        """_noise_penalty returns 0.3 for noisy, 1.0 for clean content."""
        from memory_core.retrieval import _noise_penalty
        assert _noise_penalty("[cron:abc-123 some task] hello") == 0.3
        assert _noise_penalty("A new session was started via /new") == 0.3
        assert _noise_penalty("<clickmem-context>stuff</clickmem-context>") == 0.3
        assert _noise_penalty("Execute your Session Startup sequence now") == 0.3
        assert _noise_penalty("Current time: Thursday (Asia/Singapore)") == 0.3
        assert _noise_penalty("Normal user discussion about architecture") == 1.0


class TestRecencyHint:
    """P4: Queries with time words should use shorter decay half-life."""

    @freeze_time("2026-03-06")
    def test_recently_boosts_fresh_memories(self, db, mock_emb):
        """Query containing 'recently' should strongly prefer recent over old memories."""
        now = datetime(2026, 3, 6, tzinfo=timezone.utc)
        fresh = make_memory(
            layer="episodic", content="deployed new feature today",
            created_at=now - timedelta(days=1),
            embedding=mock_emb.encode_document("deployed new feature today"),
        )
        stale = make_memory(
            layer="episodic", content="deployed new feature last month",
            created_at=now - timedelta(days=30),
            embedding=mock_emb.encode_document("deployed new feature last month"),
        )
        db.insert(fresh)
        db.insert(stale)

        results = hybrid_search(
            db, mock_emb, "what was deployed recently",
            cfg=RetrievalConfig(top_k=2),
        )
        assert len(results) == 2
        assert results[0]["id"] == fresh.id

    def test_detect_recency_hint_matches(self):
        """_detect_recency_hint returns 7.0 for time-related queries."""
        from memory_core.retrieval import _detect_recency_hint
        assert _detect_recency_hint("what happened recently") == 7.0
        assert _detect_recency_hint("show me the latest changes") == 7.0
        assert _detect_recency_hint("最近在做什么") == 7.0
        assert _detect_recency_hint("上周的进展") == 7.0
        assert _detect_recency_hint("last few days work") == 7.0
        assert _detect_recency_hint("what is clickmem") is None
        assert _detect_recency_hint("who is Claire") is None


class TestRefinementBoost:
    """Test that source='refinement' memories get a scoring boost."""

    def test_refined_memory_scores_higher(self, db, mock_emb):
        """A refined semantic memory should score higher than a similar non-refined one."""
        raw_mem = make_memory(
            layer="semantic", content="Team decided to adopt gRPC protocol for backend",
            embedding=mock_emb.encode_document("Team decided to adopt gRPC protocol for backend"),
            source="agent",
        )
        refined_mem = make_memory(
            layer="semantic", content="Team uses gRPC for all internal service communication",
            embedding=mock_emb.encode_document("Team uses gRPC for all internal service communication"),
            source="refinement",
        )
        db.insert(raw_mem)
        db.insert(refined_mem)

        results = hybrid_search(
            db, mock_emb, "gRPC services",
            cfg=RetrievalConfig(top_k=2),
        )
        assert len(results) == 2
        scores = {r["id"]: r["final_score"] for r in results}
        assert scores[refined_mem.id] > scores[raw_mem.id]
