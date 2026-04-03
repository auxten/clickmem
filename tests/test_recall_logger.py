"""Tests for recall_logger module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


class TestRecallLogger:

    def test_log_writes_jsonl(self, tmp_path):
        """A valid JSONL entry is appended with full result content."""
        log_dir = tmp_path / "logs"
        import memory_core.recall_logger as rl

        with patch.object(rl, "_log_dir", return_value=log_dir):
            rl.log_recall(
                query="test query",
                project_id="proj-123",
                session_id="sess-456",
                results=[
                    {"id": "id-1", "entity_type": "decision", "score": 0.95,
                     "content": "Some decision content"},
                    {"id": "id-2", "entity_type": "principle", "score": 0.80,
                     "content": "Some principle"},
                ],
                latency_ms=12.3,
                keywords=["test", "query"],
                expanded_terms=["test query"],
                named_entities=[],
            )

        log_file = log_dir / "recall.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["type"] == "recall"
        assert entry["query"] == "test query"
        assert entry["project_id"] == "proj-123"
        assert entry["result_count"] == 2
        assert entry["top_score"] == 0.95
        assert entry["latency_ms"] == 12.3
        assert entry["keywords"] == ["test", "query"]
        assert len(entry["results"]) == 2
        assert entry["results"][0]["content"] == "Some decision content"

    def test_log_ingest(self, tmp_path):
        """Ingest events are logged to ingest.jsonl."""
        log_dir = tmp_path / "logs"
        import memory_core.recall_logger as rl

        with patch.object(rl, "_log_dir", return_value=log_dir):
            rl.log_ingest(
                session_id="sess-789",
                source="cursor",
                extracted={
                    "episodes": ["ep1", "ep2"],
                    "decisions": ["d1"],
                    "principles": [],
                    "facts": ["f1"],
                },
                latency_ms=500.0,
            )

        log_file = log_dir / "ingest.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["type"] == "ingest"
        assert entry["session_id"] == "sess-789"
        assert entry["source"] == "cursor"
        assert entry["extracted"]["episodes"] == 2
        assert entry["extracted"]["decisions"] == 1
        assert entry["extracted"]["facts"] == 1

    def test_log_error(self, tmp_path):
        """Error events are logged to error.jsonl."""
        log_dir = tmp_path / "logs"
        import memory_core.recall_logger as rl

        with patch.object(rl, "_log_dir", return_value=log_dir):
            rl.log_error(
                operation="ceo_search",
                error="Connection timeout",
                context={"query": "test"},
            )

        log_file = log_dir / "error.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["type"] == "error"
        assert entry["operation"] == "ceo_search"
        assert entry["error"] == "Connection timeout"

    def test_log_rotation(self, tmp_path):
        """File is rotated when exceeding max size."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "recall.jsonl"
        rotated = log_dir / "recall.jsonl.1"

        import memory_core.recall_logger as rl
        old_max = rl._MAX_SIZE
        rl._MAX_SIZE = 100  # 100 bytes for testing

        with patch.object(rl, "_log_dir", return_value=log_dir):
            try:
                # Write enough to exceed 100 bytes
                log_file.write_text("x" * 150)
                rl.log_recall(
                    query="after rotation",
                    project_id="", session_id="",
                    results=[], latency_ms=1.0,
                )
            finally:
                rl._MAX_SIZE = old_max

        assert rotated.exists()
        assert log_file.exists()
        # New file should contain only the new entry
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["query"] == "after rotation"
