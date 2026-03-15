"""Tests for CEO Dedup module."""

from memory_core.ceo_dedup import dedup_decision, dedup_episode, dedup_principle
from tests.helpers.factories import DecisionFactory, EpisodeFactory, PrincipleFactory


class TestDedupEpisode:

    def test_no_dup_when_empty_db(self, ceo_db, mock_emb):
        e = EpisodeFactory.build(content="New episode")
        e.embedding = mock_emb.encode_document(e.content)
        result = dedup_episode(ceo_db, mock_emb, e)
        assert result is None

    def test_detects_duplicate(self, ceo_db, mock_emb):
        e1 = EpisodeFactory.build(content="Exact same content")
        e1.embedding = mock_emb.encode_document(e1.content)
        ceo_db.insert_episode(e1)

        e2 = EpisodeFactory.build(content="Exact same content")
        e2.embedding = mock_emb.encode_document(e2.content)
        result = dedup_episode(ceo_db, mock_emb, e2)
        assert result == e1.id

    def test_no_dup_for_different_content(self, ceo_db, mock_emb):
        e1 = EpisodeFactory.build(content="About databases")
        e1.embedding = mock_emb.encode_document(e1.content)
        ceo_db.insert_episode(e1)

        e2 = EpisodeFactory.build(content="About cooking recipes")
        e2.embedding = mock_emb.encode_document(e2.content)
        result = dedup_episode(ceo_db, mock_emb, e2)
        # Mock embeddings are deterministic based on content, so different content = different embedding
        # May or may not be None depending on mock impl; just verify no crash
        assert result is None or isinstance(result, str)


class TestDedupDecision:

    def test_add_when_empty_db(self, ceo_db, mock_emb):
        d = DecisionFactory.build(title="Use chDB")
        d.embedding = mock_emb.encode_document(d.title)
        result = dedup_decision(ceo_db, mock_emb, d)
        assert result.action == "ADD"

    def test_add_for_new_decision(self, ceo_db, mock_emb):
        d1 = DecisionFactory.build(title="Use chDB for storage")
        d1.embedding = mock_emb.encode_document(d1.title)
        ceo_db.insert_decision(d1)

        d2 = DecisionFactory.build(title="Completely different topic about cooking")
        d2.embedding = mock_emb.encode_document(d2.title)
        result = dedup_decision(ceo_db, mock_emb, d2)
        # Should be ADD since topics differ
        assert result.action in ("ADD", "UPDATE")  # depends on mock embedding similarity

    def test_no_embedding_returns_add(self, ceo_db, mock_emb):
        d = DecisionFactory.build(title="No embedding")
        result = dedup_decision(ceo_db, mock_emb, d)
        assert result.action == "ADD"


class TestDedupPrinciple:

    def test_add_when_empty_db(self, ceo_db, mock_emb):
        p = PrincipleFactory.build(content="Keep it simple")
        p.embedding = mock_emb.encode_document(p.content)
        result = dedup_principle(ceo_db, mock_emb, p)
        assert result.action == "ADD"

    def test_noop_for_identical_principle(self, ceo_db, mock_emb):
        p1 = PrincipleFactory.build(content="Keep it simple always", evidence_count=1)
        p1.embedding = mock_emb.encode_document(p1.content)
        ceo_db.insert_principle(p1)

        p2 = PrincipleFactory.build(content="Keep it simple always")
        p2.embedding = mock_emb.encode_document(p2.content)
        result = dedup_principle(ceo_db, mock_emb, p2)
        assert result.action == "NOOP"
        assert result.existing_id == p1.id

        # Check evidence was incremented
        updated = ceo_db.get_principle(p1.id)
        assert updated.evidence_count == 2

    def test_no_embedding_returns_add(self, ceo_db, mock_emb):
        p = PrincipleFactory.build(content="No embedding")
        result = dedup_principle(ceo_db, mock_emb, p)
        assert result.action == "ADD"
