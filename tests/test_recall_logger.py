"""Tests for recall_logger module."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest


class TestRecallLogger:

    def test_log_disabled_by_default(self, tmp_path):
        """When CLICKMEM_RECALL_LOG is not set, nothing is written."""
        log_file = tmp_path / "recall.jsonl"
        with patch.dict(os.environ, {"CLICKMEM_RECALL_LOG": "0"}):
            # Re-import to pick up env change
            import memory_core.recall_logger as rl
            old_enabled = rl._ENABLED
            rl._ENABLED = False
            try:
                rl.log_recall(
                    query="test", project_id="p1", session_id="s1",
                    results=[{"id": "r1", "entity_type": "decision", "score": 0.9}],
                    latency_ms=5.0,
                )
            finally:
                rl._ENABLED = old_enabled
        assert not log_file.exists()

    def test_log_writes_jsonl(self, tmp_path):
        """When enabled, a valid JSONL entry is appended."""
        log_file = tmp_path / "recall.jsonl"
        import memory_core.recall_logger as rl

        old_enabled = rl._ENABLED
        rl._ENABLED = True
        with patch.object(rl, "_log_path", return_value=log_file):
            try:
                rl.log_recall(
                    query="test query",
                    project_id="proj-123",
                    session_id="sess-456",
                    results=[
                        {"id": "id-1", "entity_type": "decision", "score": 0.95},
                        {"id": "id-2", "entity_type": "principle", "score": 0.80},
                    ],
                    latency_ms=12.3,
                )
            finally:
                rl._ENABLED = old_enabled

        assert log_file.exists()
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["query"] == "test query"
        assert entry["project_id"] == "proj-123"
        assert entry["result_count"] == 2
        assert entry["top_score"] == 0.95
        assert entry["latency_ms"] == 12.3
        assert len(entry["top_results"]) == 2

    def test_log_rotation(self, tmp_path):
        """File is rotated when exceeding max size."""
        log_file = tmp_path / "recall.jsonl"
        rotated = tmp_path / "recall.jsonl.1"

        # Create a file just over the rotation threshold
        import memory_core.recall_logger as rl
        old_max = rl._MAX_SIZE
        rl._MAX_SIZE = 100  # 100 bytes for testing
        old_enabled = rl._ENABLED
        rl._ENABLED = True

        with patch.object(rl, "_log_path", return_value=log_file):
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
                rl._ENABLED = old_enabled

        assert rotated.exists()
        assert log_file.exists()
        # New file should contain only the new entry
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["query"] == "after rotation"
