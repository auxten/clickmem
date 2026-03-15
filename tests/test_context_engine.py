"""Tests for Context Engine module."""

from memory_core.context_engine import build_ceo_context
from tests.helpers.factories import (
    DecisionFactory,
    EpisodeFactory,
    PrincipleFactory,
    ProjectFactory,
)


class TestBuildCEOContext:

    def test_empty_db_returns_empty(self, ceo_db, mock_emb):
        result = build_ceo_context(ceo_db, mock_emb)
        assert result == ""

    def test_with_project(self, ceo_db, mock_emb):
        p = ProjectFactory.build(name="TestProj", description="A test project", status="building")
        ceo_db.insert_project(p)
        result = build_ceo_context(ceo_db, mock_emb, project_id=p.id)
        assert "<clickmem-context>" in result
        assert "TestProj" in result

    def test_includes_principles(self, ceo_db, mock_emb):
        pr = PrincipleFactory.build(content="Always test first", confidence=0.9, project_id="")
        pr.embedding = mock_emb.encode_document(pr.content)
        ceo_db.insert_principle(pr)
        result = build_ceo_context(ceo_db, mock_emb)
        assert "Principles" in result
        assert "Always test first" in result

    def test_includes_decisions(self, ceo_db, mock_emb):
        d = DecisionFactory.build(title="Use Python", choice="Python 3.11", project_id="")
        ceo_db.insert_decision(d)
        result = build_ceo_context(ceo_db, mock_emb)
        assert "Decisions" in result
        assert "Use Python" in result

    def test_includes_episodes(self, ceo_db, mock_emb):
        e = EpisodeFactory.build(content="Built the database layer", project_id="")
        ceo_db.insert_episode(e)
        result = build_ceo_context(ceo_db, mock_emb)
        assert "Activity" in result
        assert "database layer" in result

    def test_with_task_hint(self, populated_ceo_db, mock_emb):
        result = build_ceo_context(
            populated_ceo_db, mock_emb,
            task_hint="database storage implementation",
        )
        assert "<clickmem-context>" in result
        # Should have semantic search section
        assert "Relevant Context" in result

    def test_token_budget_respected(self, populated_ceo_db, mock_emb):
        result = build_ceo_context(
            populated_ceo_db, mock_emb,
            max_tokens=100,  # very small budget
        )
        # Should still produce something but be short
        assert len(result) < 2000  # 100 tokens * 4 chars + overhead

    def test_wraps_in_tags(self, populated_ceo_db, mock_emb):
        result = build_ceo_context(populated_ceo_db, mock_emb)
        assert result.startswith("<clickmem-context>")
        assert result.endswith("</clickmem-context>")
