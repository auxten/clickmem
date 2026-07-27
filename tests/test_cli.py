"""CLI validation for write metadata."""

from __future__ import annotations

import json
import os
from pathlib import Path

try:  # tomllib is stdlib on 3.11+; fall back to tomli on 3.10
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from typer.testing import CliRunner

from clickmem.cli import app
from clickmem.transport import LocalTransport


def test_package_version_matches_pyproject():
    import clickmem

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text())["project"]

    assert clickmem.__version__ == project["version"]


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


def test_recall_accepts_agent_friendly_aliases_and_json(backend):
    LocalTransport().remember(
        "cli alias deployment workflow fixture",
        kind="fact",
        project_id="p1",
        privacy="public",
        tags=["deployment", "workflow"],
    )

    result = CliRunner().invoke(
        app,
        [
            "recall",
            "cli alias deployment workflow fixture",
            "--project",
            "p1",
            "--tags",
            "deployment,workflow",
            "--tag-mode",
            "all",
            "--timeout",
            "5",
            "--json",
        ],
    )

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["hits"]
    assert body["hits"][0]["project_id"] == "p1"
    assert set(["deployment", "workflow"]).issubset(set(body["hits"][0]["tags"]))


def test_recall_accepts_canonical_aliases_with_repeated_tags(backend):
    LocalTransport().remember(
        "cli canonical deployment fixture",
        kind="fact",
        project_id="p1",
        privacy="public",
        tags=["deployment"],
    )

    result = CliRunner().invoke(
        app,
        [
            "recall",
            "cli canonical deployment fixture",
            "--project-id",
            "p1",
            "--tag",
            "deployment",
            "--timeout-seconds",
            "5",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["hits"]


def test_recall_trace_accepts_project_and_timeout_aliases(backend):
    LocalTransport().remember(
        "cli trace alias fixture",
        kind="fact",
        project_id="p1",
        privacy="public",
        tags=["trace"],
    )

    result = CliRunner().invoke(
        app,
        [
            "recall-trace",
            "cli trace alias fixture",
            "--project",
            "p1",
            "--tags",
            "trace",
            "--timeout",
            "5",
            "--json",
        ],
    )

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["filters"]["project_id"] == "p1"
    assert body["filters"]["tags"] == ["trace"]


def test_list_accepts_project_id_alias_and_json(backend):
    LocalTransport().remember(
        "cli list alias fixture",
        kind="fact",
        project_id="p1",
        privacy="public",
        tags=["list"],
    )

    result = CliRunner().invoke(app, ["list", "--project-id", "p1", "--json"])

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["total"] == 1
    assert body["items"][0]["project_id"] == "p1"


def test_service_status_splits_local_and_remote_health(monkeypatch, backend):
    from clickmem import cli as cli_mod
    from clickmem import service as service_mod

    monkeypatch.setenv("CLICKMEM_REMOTE", "http://mini.local:9527")
    monkeypatch.setattr(service_mod, "status", lambda: {"installed": False})

    def fake_probe(url: str, api_key: str | None) -> dict[str, object]:
        return {"ok": url == "http://mini.local:9527", "url": url}

    monkeypatch.setattr(cli_mod, "_probe_http_health", fake_probe)

    result = CliRunner().invoke(app, ["service", "status"])

    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["active_transport"]["kind"] == "remote"
    assert body["local_service"]["installed"] is False
    assert body["local_health"]["ok"] is False
    assert body["remote_health"]["ok"] is True


def test_service_install_accepts_host_port_flags(monkeypatch):
    from clickmem import service as service_mod

    observed: dict[str, str | None] = {}

    def fake_install() -> dict[str, object]:
        observed["host"] = os.environ.get("CLICKMEM_SERVER_HOST")
        observed["port"] = os.environ.get("CLICKMEM_SERVER_PORT")
        return {"ok": True, "installed": True}

    monkeypatch.setattr(service_mod, "install", fake_install)

    result = CliRunner().invoke(
        app,
        ["service", "install", "--host", "0.0.0.0", "--port", "9528"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True
    assert observed == {"host": "0.0.0.0", "port": "9528"}
