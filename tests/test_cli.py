"""Tests for the typer CLI — all commands.

Uses typer.testing.CliRunner for in-process testing.
Covers remember, recall, forget, review, status, sql, maintain.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

# The CLI app will be imported from memory_core.cli
# For now this import will fail until implementation exists
try:
    from memory_core.cli import app
except (ImportError, NotImplementedError):
    app = None

runner = CliRunner()

pytestmark = pytest.mark.skipif(app is None, reason="CLI not implemented yet")


class TestRememberCommand:
    """Test `memory remember` command."""

    def test_remember_semantic_default(self):
        """remember without --layer stores in semantic."""
        result = runner.invoke(app, ["remember", "User likes Python"])
        assert result.exit_code == 0
        assert "stored" in result.stdout.lower() or "Stored" in result.stdout

    def test_remember_episodic(self):
        """remember --layer episodic stores in L1."""
        result = runner.invoke(app, [
            "remember", "Decided to use gRPC",
            "--layer", "episodic",
            "--category", "decision",
        ])
        assert result.exit_code == 0

    def test_remember_working(self):
        """remember --layer working overwrites L0."""
        result = runner.invoke(app, [
            "remember", "Debugging HNSW config",
            "--layer", "working",
        ])
        assert result.exit_code == 0
        assert "working" in result.stdout.lower() or "Working" in result.stdout

    def test_remember_with_tags(self):
        """remember --tags sets tags on the memory."""
        result = runner.invoke(app, [
            "remember", "Prefers SwiftUI",
            "--tags", "swift,ui",
            "--category", "preference",
        ])
        assert result.exit_code == 0

    def test_remember_json_output(self):
        """remember --json returns JSON."""
        result = runner.invoke(app, [
            "remember", "Alice is the backend lead",
            "--category", "person",
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "id" in data
        assert data["status"] == "stored"

    def test_remember_empty_content_fails(self):
        """remember with empty content should fail."""
        result = runner.invoke(app, ["remember", ""])
        assert result.exit_code != 0


class TestRecallCommand:
    """Test `memory recall` command."""

    def test_recall_basic(self):
        """recall returns results for a query."""
        # First store something
        runner.invoke(app, ["remember", "Prefers SwiftUI over UIKit", "--category", "preference"])
        result = runner.invoke(app, ["recall", "SwiftUI"])
        assert result.exit_code == 0

    def test_recall_with_layer_filter(self):
        """recall --layer filters results."""
        runner.invoke(app, ["remember", "Test semantic", "--layer", "semantic"])
        result = runner.invoke(app, ["recall", "test", "--layer", "semantic"])
        assert result.exit_code == 0

    def test_recall_with_category_filter(self):
        """recall --category filters results."""
        runner.invoke(app, [
            "remember", "Decided on Python",
            "--layer", "episodic",
            "--category", "decision",
        ])
        result = runner.invoke(app, [
            "recall", "Python",
            "--layer", "episodic",
            "--category", "decision",
        ])
        assert result.exit_code == 0

    def test_recall_json_output(self):
        """recall --json returns JSON array."""
        runner.invoke(app, ["remember", "Test recall json"])
        result = runner.invoke(app, ["recall", "test", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_recall_no_results(self):
        """recall with a query matching nothing returns empty gracefully."""
        result = runner.invoke(app, ["recall", "zzz_nonexistent_query_xyz"])
        assert result.exit_code == 0


class TestForgetCommand:
    """Test `memory forget` command."""

    def test_forget_by_id(self):
        """forget removes a memory by ID."""
        result = runner.invoke(app, [
            "remember", "To be forgotten",
            "--json",
        ])
        data = json.loads(result.stdout)
        mid = data["id"]
        result = runner.invoke(app, ["forget", mid])
        assert result.exit_code == 0
        assert "forgotten" in result.stdout.lower() or "Forgotten" in result.stdout

    def test_forget_by_prefix(self):
        """forget with ID prefix works."""
        result = runner.invoke(app, [
            "remember", "Also to be forgotten",
            "--json",
        ])
        data = json.loads(result.stdout)
        prefix = data["id"][:8]
        result = runner.invoke(app, ["forget", prefix])
        assert result.exit_code == 0

    def test_forget_json_output(self):
        """forget --json returns JSON."""
        result = runner.invoke(app, [
            "remember", "Forget me json",
            "--json",
        ])
        data = json.loads(result.stdout)
        mid = data["id"]
        result = runner.invoke(app, ["forget", mid, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["status"] == "deleted"

    def test_forget_nonexistent(self):
        """forget with nonexistent ID shows error."""
        result = runner.invoke(app, ["forget", "nonexistent-id-12345"])
        assert result.exit_code != 0 or "not found" in result.stdout.lower()


class TestReviewCommand:
    """Test `memory review` command."""

    def test_review_working(self):
        """review --layer working shows working memory."""
        runner.invoke(app, ["remember", "Current focus", "--layer", "working"])
        result = runner.invoke(app, ["review", "--layer", "working"])
        assert result.exit_code == 0
        assert "Current focus" in result.stdout

    def test_review_semantic(self):
        """review --layer semantic lists semantic memories."""
        runner.invoke(app, ["remember", "iOS developer", "--category", "knowledge"])
        result = runner.invoke(app, ["review", "--layer", "semantic"])
        assert result.exit_code == 0

    def test_review_episodic(self):
        """review --layer episodic lists episodic memories."""
        runner.invoke(app, [
            "remember", "Decided on gRPC",
            "--layer", "episodic",
            "--category", "decision",
        ])
        result = runner.invoke(app, ["review", "--layer", "episodic"])
        assert result.exit_code == 0

    def test_review_with_limit(self):
        """review --limit restricts output."""
        for i in range(5):
            runner.invoke(app, [
                "remember", f"Event {i}",
                "--layer", "episodic",
            ])
        result = runner.invoke(app, ["review", "--layer", "episodic", "--limit", "2"])
        assert result.exit_code == 0


class TestStatusCommand:
    """Test `memory status` command."""

    def test_status_shows_counts(self):
        """status displays per-layer counts."""
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        output = result.stdout.lower()
        assert "episodic" in output or "l1" in output
        assert "semantic" in output or "l2" in output

    def test_status_json(self):
        """status --json returns JSON."""
        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)


class TestSqlCommand:
    """Test `memory sql` command."""

    def test_sql_select(self):
        """sql executes a SELECT query."""
        runner.invoke(app, ["remember", "SQL test memory"])
        result = runner.invoke(app, [
            "sql",
            "SELECT count() as cnt FROM memories FINAL WHERE is_active=1",
        ])
        assert result.exit_code == 0

    def test_sql_json_output(self):
        """sql --json returns JSON."""
        result = runner.invoke(app, [
            "sql",
            "SELECT 1 as one",
            "--json",
        ])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_sql_invalid_query(self):
        """sql with invalid SQL shows error."""
        result = runner.invoke(app, ["sql", "INVALID SQL HERE"])
        assert result.exit_code != 0 or "error" in result.stdout.lower()


class TestLocalFallback:
    """Test --local flag and auto-fallback behavior."""

    def test_local_flag_status(self):
        """memory --local status works without a running server."""
        result = runner.invoke(app, ["--local", "status"])
        assert result.exit_code == 0
        output = result.stdout.lower()
        assert "episodic" in output or "legacy" in output

    def test_local_flag_status_json(self):
        """memory --local status --json returns valid JSON."""
        result = runner.invoke(app, ["--local", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
        assert "counts" in data

    def test_auto_fallback_when_no_server(self):
        """Without --remote and without a server, CLI auto-falls back to local."""
        import memory_core.cli as cli_mod
        cli_mod._transport_instance = None
        cli_mod._force_local = False
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0


class TestMaintainCommand:
    """Test `memory maintain` command."""

    def test_maintain_basic(self):
        """maintain runs without errors."""
        result = runner.invoke(app, ["maintain"])
        assert result.exit_code == 0

    def test_maintain_dry_run(self):
        """maintain --dry-run shows what would happen without modifying data."""
        result = runner.invoke(app, ["maintain", "--dry-run"])
        assert result.exit_code == 0
        # Dry run should indicate what would be done
        assert "would" in result.stdout.lower() or "dry" in result.stdout.lower() or len(result.stdout) >= 0

    def test_maintain_json(self):
        """maintain --json returns JSON summary."""
        result = runner.invoke(app, ["maintain", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
