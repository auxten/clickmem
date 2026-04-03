"""Tests for the auto-research probe generation and evaluation pipeline."""

from __future__ import annotations

import json

import pytest

from memory_core.auto_research import (
    _truncate_text,
    build_eval_prompt,
    build_probe_prompt,
    generate_report,
    parse_probes,
    run_probes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockTransport:
    """Minimal transport stub that returns configurable recall results."""

    def __init__(self, results: list[dict] | None = None):
        self._results = results or []

    def recall(self, query: str, cfg=None, min_score: float = 0.0, enhanced: bool = False) -> list[dict]:
        return self._results


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------

class TestSampleConversationsTruncation:
    def test_short_text_unchanged(self):
        assert _truncate_text("hello world") == "hello world"

    def test_exact_limit_unchanged(self):
        text = "x" * 4000
        assert _truncate_text(text) == text

    def test_long_text_truncated(self):
        text = "A" * 3000 + "B" * 3000  # 6000 chars
        result = _truncate_text(text)
        assert "...[truncated]..." in result
        assert result.startswith("A" * 2000)
        assert result.endswith("B" * 2000)
        assert len(result) < len(text)

    def test_custom_limit(self):
        text = "x" * 200
        result = _truncate_text(text, limit=100)
        assert "...[truncated]..." in result
        assert len(result) < 200


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

class TestBuildProbePrompt:
    def test_prompt_short(self):
        convos = [
            {"source": "claude_code", "project": "myapp", "timestamp": "2025-01-15T10:00:00", "text_snippet": "some code talk"},
            {"source": "cursor", "project": "lib", "timestamp": "2025-01-16T10:00:00", "text_snippet": "more talk"},
        ]
        prompt = build_probe_prompt(convos)
        # Count instruction words (everything before first conversation marker)
        instruction_part = prompt.split("--- [")[0]
        word_count = len(instruction_part.split())
        assert word_count < 200, f"Instruction is {word_count} words, should be under 200"

    def test_prompt_contains_conversations(self):
        convos = [
            {"source": "claude_code", "project": "proj", "timestamp": "2025-01-15T00:00:00", "text_snippet": "UNIQUE_MARKER_XYZ"},
        ]
        prompt = build_probe_prompt(convos)
        assert "UNIQUE_MARKER_XYZ" in prompt
        assert "claude_code" in prompt
        assert "proj" in prompt


# ---------------------------------------------------------------------------
# Probe parsing
# ---------------------------------------------------------------------------

class TestParseProbes:
    def test_valid_json_array(self):
        llm_output = json.dumps([
            {"query": "What port does the API use?", "probe_words": ["8080", "API"], "rationale": "key config"},
            {"query": "Where is the config?", "probe_words": ["/etc/app.conf"], "rationale": "path info"},
        ])
        probes = parse_probes(llm_output)
        assert len(probes) == 2
        assert probes[0]["query"] == "What port does the API use?"
        assert probes[0]["probe_words"] == ["8080", "API"]

    def test_fenced_json(self):
        llm_output = '```json\n[{"query": "test?", "probe_words": ["foo"]}]\n```'
        probes = parse_probes(llm_output)
        assert len(probes) == 1
        assert probes[0]["query"] == "test?"

    def test_skips_invalid_entries(self):
        llm_output = json.dumps([
            {"query": "valid?", "probe_words": ["x"]},
            {"query": "", "probe_words": ["y"]},           # empty query
            {"query": "no words", "probe_words": []},       # empty probe_words
            {"something": "else"},                          # missing both
            {"query": "also valid", "probe_words": ["z"]},
        ])
        probes = parse_probes(llm_output)
        assert len(probes) == 2
        assert probes[0]["query"] == "valid?"
        assert probes[1]["query"] == "also valid"

    def test_returns_empty_on_garbage(self):
        probes = parse_probes("this is not json at all")
        assert probes == []

    def test_preamble_text_before_json(self):
        llm_output = 'Here are the probes:\n[{"query": "q?", "probe_words": ["w"]}]'
        probes = parse_probes(llm_output)
        assert len(probes) == 1


# ---------------------------------------------------------------------------
# Running probes
# ---------------------------------------------------------------------------

class TestRunProbes:
    def test_pass_all_words_found(self):
        transport = MockTransport(results=[
            {"content": "The server runs on port 8080 with API key auth", "final_score": 0.9},
        ])
        probes = [{"query": "What port?", "probe_words": ["8080", "API"]}]
        results = run_probes(probes, transport)
        assert results[0]["status"] == "pass"
        assert results[0]["found_words"] == ["8080", "API"]
        assert results[0]["missing_words"] == []

    def test_fail_no_words_found(self):
        transport = MockTransport(results=[
            {"content": "unrelated content about databases", "final_score": 0.3},
        ])
        probes = [{"query": "What port?", "probe_words": ["8080", "nginx"]}]
        results = run_probes(probes, transport)
        assert results[0]["status"] == "fail"
        assert results[0]["missing_words"] == ["8080", "nginx"]
        assert results[0]["found_words"] == []

    def test_partial_some_words_found(self):
        transport = MockTransport(results=[
            {"content": "configured port 8080 for the backend", "final_score": 0.6},
        ])
        probes = [{"query": "What port and host?", "probe_words": ["8080", "localhost"]}]
        results = run_probes(probes, transport)
        assert results[0]["status"] == "partial"
        assert "8080" in results[0]["found_words"]
        assert "localhost" in results[0]["missing_words"]

    def test_case_insensitive_matching(self):
        transport = MockTransport(results=[
            {"content": "Deploy to PRODUCTION server", "final_score": 0.8},
        ])
        probes = [{"query": "Where to deploy?", "probe_words": ["production"]}]
        results = run_probes(probes, transport)
        assert results[0]["status"] == "pass"

    def test_empty_recall_results(self):
        transport = MockTransport(results=[])
        probes = [{"query": "anything?", "probe_words": ["x"]}]
        results = run_probes(probes, transport)
        assert results[0]["status"] == "fail"

    def test_top_results_summary(self):
        transport = MockTransport(results=[
            {"content": "A" * 300, "final_score": 0.9},
            {"content": "short", "final_score": 0.5},
        ])
        probes = [{"query": "test", "probe_words": ["nonexistent"]}]
        results = run_probes(probes, transport)
        assert len(results[0]["top_results"]) == 2
        assert len(results[0]["top_results"][0]["content"]) <= 200


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_format_has_expected_sections(self):
        probe_results = [
            {"query": "Q1?", "probe_words": ["a"], "status": "pass", "found_words": ["a"], "missing_words": [], "top_results": []},
            {"query": "Q2?", "probe_words": ["b", "c"], "status": "partial", "found_words": ["b"], "missing_words": ["c"], "top_results": []},
            {"query": "Q3?", "probe_words": ["d"], "status": "fail", "found_words": [], "missing_words": ["d"],
             "top_results": [{"content": "irrelevant", "score": 0.2}]},
        ]
        report = generate_report(probe_results, date="2025-06-01")
        assert "# ClickMem Recall Research Report" in report
        assert "## Summary" in report
        assert "## Passed Probes" in report
        assert "## Partial Matches" in report
        assert "## Failed Probes" in report
        assert "Q1?" in report
        assert "Q2?" in report
        assert "Q3?" in report
        assert "33%" in report  # 1/3 pass rate

    def test_with_evaluation(self):
        probe_results = [
            {"query": "Q?", "probe_words": ["x"], "status": "fail", "found_words": [], "missing_words": ["x"],
             "top_results": [{"content": "nope", "score": 0.1}]},
        ]
        evaluation = {
            "failure_analysis": [{"query": "Q?", "category": "data_gap", "explanation": "never stored"}],
            "systemic_issues": ["Missing ingestion for cursor sessions"],
            "parameter_suggestions": ["Increase top_k to 10"],
        }
        report = generate_report(probe_results, evaluation=evaluation, date="2025-06-01")
        assert "## Failure Analysis" in report
        assert "## Systemic Issues" in report
        assert "## Parameter Suggestions" in report
        assert "data_gap" in report
        assert "Missing ingestion" in report

    def test_empty_results(self):
        report = generate_report([], date="2025-06-01")
        assert "# ClickMem Recall Research Report" in report
        assert "Total probes | 0" in report


# ---------------------------------------------------------------------------
# Eval prompt
# ---------------------------------------------------------------------------

class TestBuildEvalPrompt:
    def test_includes_failed_probes(self):
        failed = [
            {"query": "Where is config?", "status": "fail", "missing_words": ["config.yaml"],
             "top_results": [{"content": "unrelated stuff", "score": 0.1}]},
        ]
        prompt = build_eval_prompt(failed)
        assert "Where is config?" in prompt
        assert "config.yaml" in prompt
        assert "embedding" in prompt  # category mentioned in instruction
