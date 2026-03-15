"""Tests for CEO Retrieval module."""

from memory_core.ceo_retrieval import ceo_search
from tests.helpers.factories import DecisionFactory, EpisodeFactory, PrincipleFactory


class TestCEOSearch:

    def test_empty_query_returns_empty(self, ceo_db, mock_emb):
        results = ceo_search(ceo_db, mock_emb, "")
        assert results == []

    def test_search_across_entities(self, ceo_db, mock_emb):
        d = DecisionFactory.build(title="Use chDB")
        d.embedding = mock_emb.encode_document("Use chDB for storage")
        ceo_db.insert_decision(d)

        p = PrincipleFactory.build(content="Local-first always")
        p.embedding = mock_emb.encode_document("Local-first always")
        ceo_db.insert_principle(p)

        e = EpisodeFactory.build(content="Set up database")
        e.embedding = mock_emb.encode_document("Set up database")
        ceo_db.insert_episode(e)

        results = ceo_search(ceo_db, mock_emb, "database storage")
        assert len(results) >= 1
        entity_types = {r["entity_type"] for r in results}
        assert len(entity_types) >= 1

    def test_filter_by_entity_type(self, ceo_db, mock_emb):
        d = DecisionFactory.build(title="Test decision")
        d.embedding = mock_emb.encode_document("Test decision")
        ceo_db.insert_decision(d)

        e = EpisodeFactory.build(content="Test episode")
        e.embedding = mock_emb.encode_document("Test episode")
        ceo_db.insert_episode(e)

        results = ceo_search(ceo_db, mock_emb, "test", entity_types=["decisions"])
        entity_types = {r["entity_type"] for r in results}
        assert "episode" not in entity_types

    def test_populated_db_search(self, populated_ceo_db, mock_emb):
        results = ceo_search(populated_ceo_db, mock_emb, "chDB storage", top_k=5)
        assert len(results) >= 1
        assert all("score" in r for r in results)
        # Results should be sorted by score desc
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)
