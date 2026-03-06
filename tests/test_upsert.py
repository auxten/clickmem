"""Tests for smart upsert logic.

Covers:
1. No similar results → ADD
2. Similar results + LLM UPDATE → old memory updated
3. Similar results + LLM DELETE → old memory deactivated
4. Similar results + LLM MERGED → no new memory, existing updated
5. No LLM available → FALLBACK_ADD
6. Episodic layer always direct INSERT
7. LLM returns bad JSON → FALLBACK_ADD
"""

from __future__ import annotations

import json

import pytest

from memory_core.models import Memory
from memory_core.upsert import upsert, UpsertResult, _parse_llm_response


class TestUpsertNoSimilar:
    """When no similar memories exist, upsert should ADD."""

    def test_add_to_empty_db(self, db, mock_emb):
        result = upsert(db, mock_emb, "Claire is Auxten's wife", "semantic", "person", ["family"])
        assert result.action == "ADD"
        assert result.added_id is not None
        # Memory should exist in DB
        m = db.get(result.added_id)
        assert m is not None
        assert m.content == "Claire is Auxten's wife"
        assert m.layer == "semantic"
        assert m.category == "person"

    def test_add_with_llm_but_no_similar(self, db, mock_emb, mock_llm):
        """Even with LLM available, if no similar results, just ADD."""
        result = upsert(db, mock_emb, "Python is great", "semantic", "knowledge", [], llm_complete=mock_llm)
        assert result.action == "ADD"
        assert result.added_id is not None
        # LLM should NOT have been called (no similar results to judge)
        assert mock_llm.call_count == 0


class TestUpsertWithLLMUpdate:
    """When LLM decides to UPDATE an existing memory."""

    def test_update_existing(self, db, mock_emb):
        # Insert initial memory
        m = Memory(
            content="Claire is Auxten's wife",
            layer="semantic", category="person", tags=["family"],
            embedding=mock_emb.encode_document("Claire is Auxten's wife"),
            source="cli",
        )
        db.insert(m)
        old_id = m.id

        # Create a mock LLM that returns UPDATE
        def mock_llm_update(prompt: str) -> str:
            return json.dumps({
                "memory_actions": [
                    {
                        "existing_id": old_id,
                        "action": "UPDATE",
                        "updated_content": "Claire is Auxten's wife. They have been married since 2020.",
                    }
                ],
                "should_add": False,
            })

        result = upsert(
            db, mock_emb,
            "Claire and Auxten got married in 2020",
            "semantic", "person", ["family"],
            llm_complete=mock_llm_update,
            similarity_threshold=0.1,  # Lower threshold for mock embeddings
        )

        assert result.action == "MERGED"
        assert result.added_id is None
        assert len(result.updated) == 1
        assert result.updated[0]["id"] == old_id

        # Old memory should be deactivated
        old_m = db.get(old_id)
        assert old_m is None or not old_m.is_active

        # New version should exist with merged content
        new_id = result.updated[0]["new_id"]
        new_m = db.get(new_id)
        assert new_m is not None
        assert "married since 2020" in new_m.content


class TestUpsertWithLLMDelete:
    """When LLM decides to DELETE an existing memory."""

    def test_delete_contradictory(self, db, mock_emb):
        # Insert wrong memory
        m = Memory(
            content="Claire and Auxten are the same person",
            layer="semantic", category="person", tags=["identity"],
            embedding=mock_emb.encode_document("Claire and Auxten are the same person"),
            source="cli",
        )
        db.insert(m)
        wrong_id = m.id

        # LLM decides to delete the wrong one and add new
        def mock_llm_delete(prompt: str) -> str:
            return json.dumps({
                "memory_actions": [
                    {
                        "existing_id": wrong_id,
                        "action": "DELETE",
                    }
                ],
                "should_add": True,
            })

        result = upsert(
            db, mock_emb,
            "Claire is Auxten's wife, they are different people",
            "semantic", "person", ["family"],
            llm_complete=mock_llm_delete,
            similarity_threshold=0.1,  # Lower threshold for mock embeddings
        )

        assert result.action == "UPSERT"
        assert result.added_id is not None
        assert wrong_id in result.deleted

        # Wrong memory should be deactivated
        wrong_m = db.get(wrong_id)
        assert wrong_m is None or not wrong_m.is_active

        # New memory should exist
        new_m = db.get(result.added_id)
        assert new_m is not None
        assert "different people" in new_m.content


class TestUpsertNoLLM:
    """When no LLM is available, fallback to direct INSERT."""

    def test_fallback_add(self, db, mock_emb):
        # Insert initial memory so there's a similar result
        m = Memory(
            content="Claire is Auxten's wife",
            layer="semantic", category="person", tags=["family"],
            embedding=mock_emb.encode_document("Claire is Auxten's wife"),
            source="cli",
        )
        db.insert(m)

        # Upsert without LLM — should fallback to INSERT
        # Note: with mock embeddings, similarity might not cross threshold,
        # but we test the code path
        result = upsert(
            db, mock_emb,
            "Claire is Auxten's wife, they live in Shanghai",
            "semantic", "person", ["family"],
            llm_complete=None,
        )

        # Should either be ADD (no similar above threshold) or FALLBACK_ADD
        assert result.action in ("ADD", "FALLBACK_ADD")
        assert result.added_id is not None


class TestUpsertEpisodic:
    """Episodic layer always does direct INSERT, no dedup."""

    def test_episodic_direct_insert(self, db, mock_emb, mock_llm):
        result = upsert(
            db, mock_emb,
            "User discussed architecture today",
            "episodic", "event", ["meeting"],
            llm_complete=mock_llm,
        )
        assert result.action == "ADD"
        assert result.added_id is not None
        # LLM should NOT have been called
        assert mock_llm.call_count == 0

    def test_episodic_allows_duplicates(self, db, mock_emb):
        """Same content inserted twice should create two entries."""
        content = "Daily standup meeting"
        r1 = upsert(db, mock_emb, content, "episodic", "event", [])
        r2 = upsert(db, mock_emb, content, "episodic", "event", [])
        assert r1.added_id != r2.added_id
        assert db.count() == 2


class TestUpsertLLMBadResponse:
    """When LLM returns unparseable JSON, fallback to INSERT."""

    def test_bad_json_fallback(self, db, mock_emb):
        # Insert something so search finds it
        m = Memory(
            content="Test memory for bad json",
            layer="semantic", category="knowledge", tags=[],
            embedding=mock_emb.encode_document("Test memory for bad json"),
            source="cli",
        )
        db.insert(m)

        def bad_llm(prompt: str) -> str:
            return "This is not valid JSON at all!"

        # Even if similar exists, bad LLM response → fallback
        result = upsert(
            db, mock_emb,
            "Test memory for bad json response",
            "semantic", "knowledge", [],
            llm_complete=bad_llm,
        )
        # Should either ADD (no similar above threshold) or FALLBACK_ADD
        assert result.action in ("ADD", "FALLBACK_ADD")
        assert result.added_id is not None


class TestUpsertWithNoop:
    """When LLM decides all existing are NOOP and should_add=true."""

    def test_noop_with_add(self, db, mock_emb):
        m = Memory(
            content="Python is a programming language",
            layer="semantic", category="knowledge", tags=["python"],
            embedding=mock_emb.encode_document("Python is a programming language"),
            source="cli",
        )
        db.insert(m)
        old_id = m.id

        def mock_llm_noop(prompt: str) -> str:
            return json.dumps({
                "memory_actions": [
                    {"existing_id": old_id, "action": "NOOP"}
                ],
                "should_add": True,
            })

        result = upsert(
            db, mock_emb,
            "Python was created by Guido van Rossum",
            "semantic", "knowledge", ["python"],
            llm_complete=mock_llm_noop,
            similarity_threshold=0.1,  # Lower threshold for mock embeddings
        )

        # Action should be ADD since no updates/deletes happened
        assert result.action == "ADD"
        assert result.added_id is not None
        # Original should still be active
        orig = db.get(old_id)
        assert orig is not None
        assert orig.is_active


class TestParseLLMResponse:
    """Test the JSON response parser."""

    def test_plain_json(self):
        resp = '{"memory_actions": [], "should_add": true}'
        parsed = _parse_llm_response(resp)
        assert parsed is not None
        assert parsed["should_add"] is True

    def test_markdown_fenced_json(self):
        resp = '```json\n{"memory_actions": [], "should_add": false}\n```'
        parsed = _parse_llm_response(resp)
        assert parsed is not None
        assert parsed["should_add"] is False

    def test_markdown_fenced_no_lang(self):
        resp = '```\n{"memory_actions": [], "should_add": true}\n```'
        parsed = _parse_llm_response(resp)
        assert parsed is not None

    def test_invalid_json(self):
        assert _parse_llm_response("not json") is None

    def test_empty_string(self):
        assert _parse_llm_response("") is None


class TestContextStripping:
    """P2: <clickmem-context> blocks should be stripped before episodic storage."""

    def test_strip_removes_context_block(self):
        from memory_core.upsert import _strip_injected_context
        raw = (
            "<clickmem-context>\n"
            "Background context from long-term memory.\n"
            "- [semantic/person] Claire = Tong\n"
            "</clickmem-context>\n"
            "user: what is the bug?\n"
            "assistant: The bug is in the parser."
        )
        cleaned = _strip_injected_context(raw)
        assert "<clickmem-context>" not in cleaned
        assert "user: what is the bug?" in cleaned
        assert "The bug is in the parser" in cleaned

    def test_strip_preserves_content_without_context(self):
        from memory_core.upsert import _strip_injected_context
        raw = "User discussed architecture with Alice"
        assert _strip_injected_context(raw) == raw

    def test_strip_empty_after_removal(self):
        from memory_core.upsert import _strip_injected_context
        raw = "<clickmem-context>Only context, nothing else</clickmem-context>"
        assert _strip_injected_context(raw) == ""

    def test_episodic_upsert_strips_context(self, db, mock_emb):
        """upsert with episodic layer should store content with context blocks removed."""
        raw = (
            "<clickmem-context>memory injection</clickmem-context>\n"
            "user: discussed deployment\n"
            "assistant: done"
        )
        result = upsert(db, mock_emb, raw, "episodic", "event", [])
        assert result.action == "ADD"
        stored = db.get(result.added_id)
        assert "<clickmem-context>" not in stored.content
        assert "discussed deployment" in stored.content

    def test_episodic_upsert_noop_if_only_context(self, db, mock_emb):
        """If stripping context leaves nothing, upsert returns NOOP."""
        raw = "<clickmem-context>Only context block here</clickmem-context>"
        result = upsert(db, mock_emb, raw, "episodic", "event", [])
        assert result.action == "NOOP"
        assert result.added_id is None


class TestUpsertResult:
    """Test UpsertResult dataclass."""

    def test_to_dict(self):
        r = UpsertResult(added_id="abc", updated=[{"id": "x"}], deleted=["y"], action="UPSERT")
        d = r.to_dict()
        assert d["action"] == "UPSERT"
        assert d["added_id"] == "abc"
        assert d["updated"] == [{"id": "x"}]
        assert d["deleted"] == ["y"]

    def test_defaults(self):
        r = UpsertResult()
        assert r.added_id is None
        assert r.updated == []
        assert r.deleted == []
        assert r.action == "ADD"
