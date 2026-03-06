"""Tests for json_utils — robust JSON extraction from LLM responses."""

from __future__ import annotations

import pytest

from memory_core.json_utils import extract_json, extract_json_or


class TestExtractJsonDirect:
    """Direct JSON strings parse correctly."""

    def test_plain_object(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_plain_array(self):
        assert extract_json('[1, 2, 3]') == [1, 2, 3]

    def test_empty_object(self):
        assert extract_json("{}") == {}

    def test_empty_array(self):
        assert extract_json("[]") == []

    def test_nested_object(self):
        result = extract_json('{"a": {"b": [1, 2]}}')
        assert result == {"a": {"b": [1, 2]}}


class TestExtractJsonFences:
    """JSON wrapped in markdown code fences."""

    def test_json_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_plain_fence(self):
        text = '```\n[1, 2]\n```'
        assert extract_json(text) == [1, 2]

    def test_fence_with_extra_whitespace(self):
        text = '  ```json\n  {"x": 1}  \n  ```  '
        assert extract_json(text) == {"x": 1}


class TestExtractJsonPreamble:
    """JSON preceded or followed by non-JSON text."""

    def test_text_before_json(self):
        text = 'Here is the result:\n{"status": "ok"}'
        assert extract_json(text) == {"status": "ok"}

    def test_text_after_json(self):
        text = '{"status": "ok"}\nHope this helps!'
        assert extract_json(text) == {"status": "ok"}

    def test_text_surrounding_json(self):
        text = 'Result:\n[{"id": 1}]\nDone.'
        assert extract_json(text) == [{"id": 1}]

    def test_fence_with_preamble(self):
        text = 'Sure, here you go:\n```json\n{"a": 1}\n```\nLet me know!'
        assert extract_json(text) == {"a": 1}


class TestExtractJsonExpectType:
    """The expect parameter filters by type."""

    def test_expect_object_gets_object(self):
        assert extract_json('{"a": 1}', expect="object") == {"a": 1}

    def test_expect_object_skips_array(self):
        assert extract_json("[1, 2]", expect="object") is None

    def test_expect_array_gets_array(self):
        assert extract_json("[1, 2]", expect="array") == [1, 2]

    def test_expect_array_skips_object(self):
        assert extract_json('{"a": 1}', expect="array") is None

    def test_expect_auto_accepts_both(self):
        assert extract_json('{"a": 1}', expect="auto") == {"a": 1}
        assert extract_json("[1]", expect="auto") == [1]


class TestExtractJsonFailures:
    """Unparseable input returns None."""

    def test_empty_string(self):
        assert extract_json("") is None

    def test_plain_text(self):
        assert extract_json("no json here") is None

    def test_malformed_json(self):
        assert extract_json('{"a": }') is None

    def test_truncated_json(self):
        assert extract_json('{"a": [1, 2') is None


class TestExtractJsonOr:
    """extract_json_or returns default on failure."""

    def test_returns_parsed_on_success(self):
        assert extract_json_or('{"a": 1}', {}) == {"a": 1}

    def test_returns_default_on_failure(self):
        assert extract_json_or("bad", {"fallback": True}) == {"fallback": True}

    def test_returns_default_list(self):
        assert extract_json_or("bad", []) == []


class TestExtractJsonRealWorldLLMOutputs:
    """Patterns commonly seen from small language models."""

    def test_thinking_tags_then_json(self):
        text = '<think>Let me analyze...</think>\n{"summary": "test"}'
        assert extract_json(text) == {"summary": "test"}

    def test_json_with_trailing_explanation(self):
        text = '{"should_promote": true, "content": "fact"}\n\nI decided to promote because...'
        result = extract_json(text, expect="object")
        assert result["should_promote"] is True

    def test_multiple_json_objects_returns_first(self):
        text = '{"a": 1}\n{"b": 2}'
        result = extract_json(text, expect="object")
        assert result == {"a": 1}

    def test_upsert_response_format(self):
        text = '```json\n{"memory_actions": [{"existing_id": "abc", "action": "NOOP"}], "should_add": true}\n```'
        result = extract_json(text, expect="object")
        assert result["should_add"] is True
        assert len(result["memory_actions"]) == 1
