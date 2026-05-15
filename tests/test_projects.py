"""Project detection + cross-ref links."""

from __future__ import annotations

import os
from pathlib import Path

from clickmem import projects
from clickmem.models import Project


def test_detect_from_cwd_outside_git(tmp_path, backend):
    project = projects.detect_from_cwd(tmp_path)
    assert project.id == tmp_path.name.lower()
    assert project.name == tmp_path.name
    assert project.repo_url.startswith("file://")


def test_detect_from_cwd_uses_git_remote(tmp_path, backend, monkeypatch):
    """Simulate a git remote → repo url drives project_id."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:auxten/example.git"],
        cwd=str(repo),
        check=True,
    )
    project = projects.detect_from_cwd(repo)
    assert project.repo_url == "https://github.com/auxten/example"
    assert project.id == "auxten/example"


def test_project_id_for_uses_readable_repo_slug():
    assert projects.project_id_for("https://github.com/auxten/clickmem") == "auxten/clickmem"
    assert projects.project_id_for("git@github.com:auxten/clickmem.git") == "auxten/clickmem"
    assert projects.project_id_for("", "ClickMem") == "clickmem"


def test_upsert_and_get(backend):
    proj = Project(id="abc123", name="demo", repo_url="https://example.com/repo")
    projects.upsert(proj)
    fetched = projects.get("abc123")
    assert fetched is not None
    assert fetched.name == "demo"
    assert fetched.repo_url == "https://example.com/repo"


def test_link_creates_cross_refs(backend):
    a, b = projects.link("p1", "p2", reason="shared library")
    assert "p2" in a.allowed_cross_refs
    assert "p1" in b.allowed_cross_refs
    assert projects.allowed_cross_refs("p1", "p2") is True
    assert projects.allowed_cross_refs("p1", "p3") is False


def test_list_all(backend):
    projects.upsert(Project(id="x1", name="x1"))
    projects.upsert(Project(id="x2", name="x2"))
    names = {p.id for p in projects.list_all()}
    assert {"x1", "x2"}.issubset(names)
