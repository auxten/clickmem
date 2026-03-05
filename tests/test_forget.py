"""Tests for enhanced forget command — UUID, prefix, and content search fallback."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

try:
    from memory_core.cli import app
except (ImportError, NotImplementedError):
    app = None

runner = CliRunner()

pytestmark = pytest.mark.skipif(app is None, reason="CLI not implemented yet")


class TestForgetByUUID:
    """Exact UUID match (existing behavior)."""

    def test_forget_by_uuid(self):
        result = runner.invoke(app, ["remember", "UUID forget test", "--json", "--no-upsert"])
        data = json.loads(result.stdout)
        mid = data["id"]

        result = runner.invoke(app, ["forget", mid, "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["status"] == "deleted"
        assert out["id"] == mid
        assert out["content"] == "UUID forget test"


class TestForgetByPrefix:
    """Short prefix match (existing behavior)."""

    def test_forget_by_prefix(self):
        result = runner.invoke(app, ["remember", "Prefix forget test", "--json", "--no-upsert"])
        data = json.loads(result.stdout)
        prefix = data["id"][:8]

        result = runner.invoke(app, ["forget", prefix, "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["status"] == "deleted"
        assert out["content"] == "Prefix forget test"


class TestForgetByContent:
    """Content search fallback (new feature)."""

    def test_forget_by_content(self):
        """Non-UUID input triggers content search and deletes matching memory."""
        runner.invoke(app, [
            "remember", "Python is the best programming language",
            "--json", "--no-upsert",
        ])

        result = runner.invoke(app, [
            "forget", "Python best programming language", "--json"
        ])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["status"] == "deleted"
        assert "Python" in out["content"]

    def test_forget_content_not_found(self):
        """Content search with no match returns error."""
        result = runner.invoke(app, [
            "forget", "zzz_completely_nonexistent_xyzzy_gibberish_qwerty", "--json"
        ])
        assert result.exit_code != 0
        out = json.loads(result.stdout)
        assert out["error"] == "not found"

    def test_forget_non_uuid_input(self):
        """Input like 'semantic/person' (not hex) goes straight to content search."""
        runner.invoke(app, [
            "remember", "Claire is Auxten's wife",
            "--category", "person",
            "--json", "--no-upsert",
        ])

        result = runner.invoke(app, [
            "forget", "semantic/person Claire wife", "--json"
        ])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["status"] == "deleted"
        assert "Claire" in out["content"]
