"""Tests for CEO Extractor module."""

import json

from memory_core.ceo_extractor import CEOExtractor, ExtractionResult
from tests.helpers.factories import ProjectFactory


class TestCEOExtractor:

    def test_extract_all_types(self, ceo_db, mock_emb, mock_llm):
        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract(
            "CEO extraction: We decided to use chDB for storage.",
            mock_llm,
            project_id="proj1",
            session_id="sess1",
        )
        assert isinstance(result, ExtractionResult)
        assert len(result.episode_ids) >= 1
        assert len(result.decision_ids) >= 1
        assert len(result.principle_ids) >= 1

    def test_extract_empty_input(self, ceo_db, mock_emb, mock_llm):
        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract("", mock_llm)
        assert result.episode_ids == []
        assert result.decision_ids == []
        assert result.principle_ids == []

    def test_extract_stores_in_db(self, ceo_db, mock_emb, mock_llm):
        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract(
            "CEO extraction: important conversation",
            mock_llm,
            project_id="proj1",
        )
        counts = ceo_db.count_all()
        assert counts["episodes"] >= 1
        assert counts["decisions"] >= 1

    def test_extract_with_project_update(self, ceo_db, mock_emb):
        p = ProjectFactory.build(status="building")
        ceo_db.insert_project(p)

        def llm_with_update(prompt):
            return json.dumps([
                {"type": "project_update", "field": "status", "new_value": "launched"},
            ])

        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract(
            "CEO extraction: we launched the project!",
            llm_with_update,
            project_id=p.id,
        )
        assert len(result.project_updates) == 1
        updated = ceo_db.get_project(p.id)
        assert updated.status == "launched"

    def test_extract_low_confidence_principle_skipped(self, ceo_db, mock_emb):
        def llm_low_confidence(prompt):
            return json.dumps([
                {"type": "principle", "content": "Maybe do X", "domain": "tech", "confidence": 0.3},
            ])

        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract("CEO extraction: uncertain", llm_low_confidence)
        assert result.principle_ids == []

    def test_parse_response_handles_markdown_fences(self, ceo_db, mock_emb):
        extractor = CEOExtractor(ceo_db, mock_emb)
        raw = '```json\n[{"type": "episode", "content": "test"}]\n```'
        items = extractor._parse_response(raw)
        assert len(items) == 1
        assert items[0]["type"] == "episode"

    def test_parse_response_handles_garbage(self, ceo_db, mock_emb):
        extractor = CEOExtractor(ceo_db, mock_emb)
        items = extractor._parse_response("not json at all")
        assert items == []

    def test_llm_failure_returns_empty(self, ceo_db, mock_emb):
        def failing_llm(prompt):
            raise RuntimeError("LLM unavailable")

        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract("CEO extraction: test", failing_llm)
        assert result.episode_ids == []

    def test_decision_alternatives_as_list(self, ceo_db, mock_emb):
        """Bug fix: LLM returns alternatives as list instead of string."""
        def llm_list_alternatives(prompt):
            return json.dumps([{
                "type": "decision",
                "title": "Use PostgreSQL",
                "context": "Need relational DB",
                "choice": "PostgreSQL",
                "reasoning": ["Fast", "Reliable"],
                "alternatives": ["MySQL", "SQLite"],
                "domain": "tech",
            }])

        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract(
            "CEO extraction: database decision",
            llm_list_alternatives,
            project_id="proj1",
        )
        assert len(result.decision_ids) == 1
        # Verify it was stored (no crash from list.replace())
        counts = ceo_db.count_all()
        assert counts["decisions"] >= 1

    def test_decision_reasoning_as_list(self, ceo_db, mock_emb):
        """Bug fix: LLM returns reasoning as list instead of string."""
        def llm_list_reasoning(prompt):
            return json.dumps([{
                "type": "decision",
                "title": "Use Redis for caching",
                "context": "Need fast cache",
                "choice": "Redis",
                "reasoning": ["Low latency", "Built-in TTL"],
                "alternatives": "Memcached",
                "domain": "tech",
            }])

        extractor = CEOExtractor(ceo_db, mock_emb)
        result = extractor.extract(
            "CEO extraction: cache decision",
            llm_list_reasoning,
            project_id="proj1",
        )
        assert len(result.decision_ids) == 1
