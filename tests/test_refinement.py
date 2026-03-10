"""Tests for the ContinualRefinement engine."""

from __future__ import annotations

import json

import pytest

from memory_core.models import Memory
from memory_core.refinement import ContinualRefinement


class TestClusterSemantic:
    """Test semantic clustering of L2 memories."""

    def test_no_clusters_with_single_memory(self, db, mock_emb):
        m = Memory(
            content="Only one memory",
            layer="semantic",
            embedding=mock_emb.encode_document("Only one memory"),
        )
        db.insert(m)

        clusters = ContinualRefinement._cluster_semantic(db, mock_emb)
        assert len(clusters) == 0

    def test_identical_content_clusters_together(self, db, mock_emb):
        for i in range(3):
            m = Memory(
                content="User prefers Python",
                layer="semantic",
                embedding=mock_emb.encode_document("User prefers Python"),
            )
            db.insert(m)

        clusters = ContinualRefinement._cluster_semantic(db, mock_emb)
        assert len(clusters) >= 1
        assert len(clusters[0]) >= 2


class TestRefineClustersMerge:
    """Test duplicate detection and merging within clusters."""

    def test_merge_duplicates(self, db, mock_emb):
        mems = []
        for content in ["Team uses gRPC", "The team adopted gRPC"]:
            m = Memory(
                content=content,
                layer="semantic",
                category="decision",
                tags=["grpc"],
                embedding=mock_emb.encode_document(content),
            )
            db.insert(m)
            mems.append(m)

        def mock_llm(prompt):
            p = prompt.lower()
            if "are these two" in p or "same thing" in p:
                return json.dumps({"is_duplicate": True, "reason": "both about gRPC"})
            if "merge" in p:
                return json.dumps({
                    "merged": "Team adopted gRPC for service communication",
                    "category": "decision",
                    "tags": ["grpc"],
                })
            return json.dumps({"status": "ok"})

        clusters = [[mems[0], mems[1]]]
        merged = ContinualRefinement._refine_clusters(db, mock_emb, mock_llm, clusters)
        assert merged == 1

        active = db.list_by_layer("semantic")
        assert len(active) == 1
        assert active[0].source == "refinement"
        assert "gRPC" in active[0].content or "grpc" in active[0].content.lower()

    def test_no_merge_when_not_duplicate(self, db, mock_emb):
        mems = []
        for content in ["Team uses gRPC", "User prefers dark mode"]:
            m = Memory(
                content=content,
                layer="semantic",
                embedding=mock_emb.encode_document(content),
            )
            db.insert(m)
            mems.append(m)

        def mock_llm(prompt):
            if "are these two" in prompt.lower() or "same thing" in prompt.lower():
                return json.dumps({"is_duplicate": False, "reason": "different topics"})
            return json.dumps({"status": "ok"})

        clusters = [[mems[0], mems[1]]]
        merged = ContinualRefinement._refine_clusters(db, mock_emb, mock_llm, clusters)
        assert merged == 0

        active = db.list_by_layer("semantic")
        assert len(active) == 2


class TestPruneLowQuality:
    """Test inclusion bar filtering."""

    def test_prune_removes_low_quality(self, db, mock_emb):
        keep = Memory(
            content="Team architecture uses microservices",
            layer="semantic",
            embedding=mock_emb.encode_document("arch"),
            source="agent",
        )
        prune = Memory(
            content="Fix the typo on line 42",
            layer="semantic",
            embedding=mock_emb.encode_document("typo"),
            source="agent",
        )
        db.insert(keep)
        db.insert(prune)

        def mock_llm(prompt):
            if "microservices" in prompt:
                return json.dumps({"keep": True, "reason": "durable architecture fact"})
            elif "typo" in prompt:
                return json.dumps({"keep": False, "reason": "one-off task instruction"})
            return json.dumps({"keep": True, "reason": "ok"})

        pruned = ContinualRefinement._prune_low_quality(db, mock_llm)
        assert pruned == 1

        active = db.list_by_layer("semantic")
        assert len(active) == 1
        assert "microservices" in active[0].content

    def test_prune_skips_refined_memories(self, db, mock_emb):
        m = Memory(
            content="Already refined fact",
            layer="semantic",
            embedding=mock_emb.encode_document("refined"),
            source="refinement",
        )
        db.insert(m)

        call_count = 0

        def mock_llm(prompt):
            nonlocal call_count
            call_count += 1
            return json.dumps({"keep": False, "reason": "test"})

        pruned = ContinualRefinement._prune_low_quality(db, mock_llm)
        assert pruned == 0
        assert call_count == 0


class TestReextractUnprocessed:
    """Test re-extraction from unprocessed raw transcripts."""

    def test_reextract_processes_raw(self, db, mock_emb, mock_llm):
        db.insert_raw("s1", "cursor", "user: I prefer Python for all backend services\nassistant: Noted, will use Python")
        db.insert_raw("s2", "cursor", "user: The team has decided to use gRPC for services\nassistant: Will do that")

        count = ContinualRefinement._reextract_unprocessed(db, mock_emb, mock_llm)
        assert count >= 2

        db.optimize()
        unprocessed = db.list_unprocessed_raw()
        assert len(unprocessed) == 0

    def test_skips_short_raw(self, db, mock_emb, mock_llm):
        db.insert_raw("s1", "cursor", "hi")

        count = ContinualRefinement._reextract_unprocessed(db, mock_emb, mock_llm)
        assert count == 0

        db.optimize()
        assert len(db.list_unprocessed_raw()) == 0


class TestRunAll:
    """Test the full refinement pipeline."""

    def test_run_returns_stats(self, db, mock_emb, mock_llm):
        for i in range(3):
            m = Memory(
                content=f"Fact number {i}",
                layer="semantic",
                embedding=mock_emb.encode_document(f"Fact number {i}"),
                source="agent",
            )
            db.insert(m)

        result = ContinualRefinement.run(db, mock_emb, mock_llm)
        assert "reextracted" in result
        assert "clusters_found" in result
        assert "merged" in result
        assert "pruned" in result
