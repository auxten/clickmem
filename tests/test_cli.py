"""CLI validation for write metadata."""

from __future__ import annotations

from typer.testing import CliRunner

from clickmem.cli import app
from clickmem.transport import LocalTransport


def test_remember_requires_scope_and_tags(backend):
    result = CliRunner().invoke(app, ["remember", "missing metadata"])

    assert result.exit_code == 2
    assert "Memory writes require explicit scope and tags" in result.output
    assert "--project owner/repo" in result.output
    assert "--global" in result.output


def test_remember_rejects_project_and_global_together(backend):
    result = CliRunner().invoke(
        app,
        [
            "remember",
            "ambiguous scope",
            "--project",
            "auxten/clickmem",
            "--global",
            "--tag",
            "workflow",
        ],
    )

    assert result.exit_code == 2
    assert "Choose exactly one scope" in result.output


def test_recall_accepts_tag_filter(backend):
    LocalTransport().remember(
        "cli tagged deployment fixture",
        kind="fact",
        project_id="p1",
        privacy="public",
        tags=["deployment"],
    )

    result = CliRunner().invoke(
        app,
        ["recall", "cli tagged deployment fixture", "--project-id", "p1", "--tag", "deployment"],
    )

    assert result.exit_code == 0
    assert "fixture" in result.output
    assert "p1" in result.output
