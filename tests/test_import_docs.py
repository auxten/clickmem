"""Import-docs: skip-rule classifier + AGENTS.md bullet parser + idempotency."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clickmem import import_docs


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)


def _commit(repo: Path, message: str = "snapshot") -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", "add", "-A"],
        cwd=str(repo),
        check=True,
    )
    subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-m", message, "-q"],
        cwd=str(repo),
        check=True,
    )


# ---------- AI-noise skip rules -----------------------------------------


def test_skip_large_no_history_file(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    # no git history for this AGENTS.md
    (repo / "AGENTS.md").write_text("x" * 10_000, encoding="utf-8")
    p = import_docs.plan(repo)
    paths = [s for s in p.skipped if "AGENTS.md" in s.get("path", "")]
    assert paths
    assert "large no-history" in paths[0]["reason"]


def test_skip_generated_marker(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "AGENTS.md").write_text("<!-- generated -->\n- never trust", encoding="utf-8")
    _commit(repo)
    p = import_docs.plan(repo)
    assert any("generated marker" in s["reason"] for s in p.skipped)


def test_skip_dream_reasoning_block(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "AGENTS.md").write_text(
        "# overview\n\n## Reasoning\n\nI think we should...", encoding="utf-8"
    )
    _commit(repo)
    p = import_docs.plan(repo)
    assert any("Dream Reasoning" in s["reason"] for s in p.skipped)


def test_skip_bullet_noise_pattern(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    # > 5 bullets, > 80% bullet lines, avg > 200 chars
    bullets = "\n".join(f"- {'word ' * 60}" for _ in range(6))
    (repo / "AGENTS.md").write_text(bullets, encoding="utf-8")
    _commit(repo)
    p = import_docs.plan(repo)
    assert any("bullet-noise" in s["reason"] for s in p.skipped)


def test_accept_normal_agents_md(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "AGENTS.md").write_text(
        "## Principles\n\n- always test before pushing\n- avoid force-push to main\n",
        encoding="utf-8",
    )
    _commit(repo)
    p = import_docs.plan(repo)
    accepted = [a for a in p.accepted if a.abs_path.name.lower() == "agents.md"]
    assert accepted


# ---------- AGENTS.md bullet parser -------------------------------------


def test_agents_md_each_bullet_becomes_one_memory(tmp_path, backend):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "AGENTS.md").write_text(
        "## Workflow\n\n- ship small commits\n- write unit tests\n"
        "## Hygiene\n\n- no force-push to main\n",
        encoding="utf-8",
    )
    _commit(repo)
    res = import_docs.run(repo)
    assert res["ok"]
    assert res["accepted"] == 1  # one file
    bullet_results = res["ingested"][0]["results"]
    contents = {r["status"] for r in bullet_results}
    assert "added" in contents
    assert len(bullet_results) == 3


# ---------- Idempotency on git sha change -------------------------------


def test_idempotent_reimport_unchanged(tmp_path, backend):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    (repo / "AGENTS.md").write_text(
        "## A\n\n- alpha rule\n", encoding="utf-8"
    )
    _commit(repo)
    first = import_docs.run(repo)
    second = import_docs.run(repo)
    # The first run ingests; the second short-circuits on source_ref match.
    assert any(r.get("status") == "skipped" for r in second["ingested"]) or \
           second["ingested"][0]["status"] == "skipped"


def test_idempotent_reimport_on_git_sha_change_revises_doc(tmp_path, backend):
    """A non-AGENTS doc with the same path but a new sha goes through Revise."""
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    rules_dir = repo / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    rules_file = rules_dir / "principles.mdc"
    rules_file.write_text("first version content body", encoding="utf-8")
    _commit(repo)

    first = import_docs.run(repo)
    accepted_first = [r for r in first["ingested"] if r.get("status") == "ingested"]
    assert accepted_first

    # mutate + commit so the blob sha changes
    rules_file.write_text("second version content body", encoding="utf-8")
    _commit(repo, "v2")

    second = import_docs.run(repo)
    # The second run should observe the new sha → either ingest (path-first)
    # or skip-then-revise via _existing_path_memory_id; what matters is the
    # underlying memory now reflects v2.
    from clickmem.memories import list_paginated
    listing = list_paginated(project_id=first["project_id"])
    contents = [m["content"] for m in listing["items"]]
    assert any("second version" in c for c in contents)
