"""Tests for Claude Code auto-memory direct sync (no LLM extraction)."""

from __future__ import annotations

import os
import time

import pytest

from memory_core.import_agent import (
    DocInfo,
    ImportState,
    _infer_domain,
    ingest_claude_memory,
    parse_claude_memory_file,
    sync_project_memories,
    sync_single_memory_file,
)
from memory_core.ceo_dedup import dedup_fact, DedupResult
from memory_core.models import Fact


# ---------------------------------------------------------------------------
# parse_claude_memory_file
# ---------------------------------------------------------------------------

_FEEDBACK_MD = """\
---
name: Auto commit, push, and deploy
description: After tests pass, always commit+push+deploy to mini without asking
type: feedback
---

When code changes are ready and tests pass, always commit, push, and deploy
to mini without asking for confirmation.

**Why:** The user prefers a fast iteration loop and finds confirmation prompts disruptive.
**How to apply:** After `pytest` passes, run git commit, push, then ssh mini deploy.
"""

_USER_MD = """\
---
name: User role
description: user is a senior engineer with deep Go expertise
type: user
---

The user is a senior engineer with 10+ years of Go experience.
Frame explanations in terms of backend analogues when discussing frontend.
"""

_REFERENCE_MD = """\
---
name: OpenClaw deploy target
description: OpenClaw deployed on mini host via Tailscale
type: reference
---

OpenClaw is deployed on the "mini" host. SSH: tong@mini, Tailscale IP: 100.86.126.80.
"""

_PROJECT_MD = """\
---
name: Auth middleware rewrite
description: Auth rewrite driven by legal compliance, not tech debt
type: project
---

The auth middleware rewrite is driven by legal/compliance requirements
around session token storage, not tech-debt cleanup.
"""

_INDEX_MD = """\
- [feedback_commit.md](feedback_commit.md) — After tests pass, commit+push+deploy
- [reference_deploy.md](reference_deploy.md) — OpenClaw deploy info
"""

_NO_FRONTMATTER = "This is just plain text without any frontmatter."

_NO_TYPE = """\
---
name: Something
description: Missing type field
---

Body text here.
"""


class TestParseClaude:
    def test_feedback(self):
        result = parse_claude_memory_file(_FEEDBACK_MD)
        assert result is not None
        assert result["type"] == "feedback"
        assert result["name"] == "Auto commit, push, and deploy"
        assert "commit, push, and deploy" in result["body"]

    def test_user(self):
        result = parse_claude_memory_file(_USER_MD)
        assert result is not None
        assert result["type"] == "user"
        assert "senior engineer" in result["body"]

    def test_reference(self):
        result = parse_claude_memory_file(_REFERENCE_MD)
        assert result is not None
        assert result["type"] == "reference"
        assert "mini" in result["body"]

    def test_project(self):
        result = parse_claude_memory_file(_PROJECT_MD)
        assert result is not None
        assert result["type"] == "project"

    def test_no_frontmatter(self):
        assert parse_claude_memory_file(_NO_FRONTMATTER) is None

    def test_no_type(self):
        assert parse_claude_memory_file(_NO_TYPE) is None

    def test_index_md(self):
        # MEMORY.md index has no frontmatter
        assert parse_claude_memory_file(_INDEX_MD) is None


# ---------------------------------------------------------------------------
# _infer_domain
# ---------------------------------------------------------------------------

class TestInferDomain:
    def test_ops(self):
        assert _infer_domain("deploy to production via CI") == "ops"

    def test_tech(self):
        assert _infer_domain("refactor the API schema") == "tech"

    def test_product(self):
        assert _infer_domain("user-focused product design") == "product"

    def test_default(self):
        assert _infer_domain("some random topic") == "management"


# ---------------------------------------------------------------------------
# ingest_claude_memory
# ---------------------------------------------------------------------------

class TestIngestClaudeMemory:
    def test_feedback_creates_principle(self, ceo_db, mock_emb):
        doc = DocInfo(
            path="/tmp/feedback_test.md", content=_FEEDBACK_MD,
            doc_type="claude_memory", project_name="test-project",
        )
        result = ingest_claude_memory(ceo_db, mock_emb, doc, project_id="proj-123")
        assert result["imported"] == 1
        assert result["entity_type"] == "principle"

        # Verify principle was inserted with project_id
        principles = ceo_db.list_principles(project_id="proj-123")
        assert len(principles) >= 1
        assert any("commit" in p.content.lower() for p in principles)

    def test_user_creates_global_principle(self, ceo_db, mock_emb):
        doc = DocInfo(
            path="/tmp/user_test.md", content=_USER_MD,
            doc_type="claude_memory", project_name="test-project",
        )
        result = ingest_claude_memory(ceo_db, mock_emb, doc, project_id="proj-123")
        assert result["imported"] == 1
        assert result["entity_type"] == "principle"

        # User type should be global (empty project_id)
        global_principles = ceo_db.list_principles(project_id="")
        assert any("senior engineer" in p.content.lower() for p in global_principles)

    def test_reference_creates_fact(self, ceo_db, mock_emb):
        doc = DocInfo(
            path="/tmp/reference_test.md", content=_REFERENCE_MD,
            doc_type="claude_memory", project_name="test-project",
        )
        result = ingest_claude_memory(ceo_db, mock_emb, doc, project_id="proj-123")
        assert result["imported"] == 1
        assert result["entity_type"] == "fact"

    def test_project_creates_fact(self, ceo_db, mock_emb):
        doc = DocInfo(
            path="/tmp/project_test.md", content=_PROJECT_MD,
            doc_type="claude_memory", project_name="test-project",
        )
        result = ingest_claude_memory(ceo_db, mock_emb, doc, project_id="proj-123")
        assert result["imported"] == 1
        assert result["entity_type"] == "fact"

    def test_memory_md_skipped(self, ceo_db, mock_emb):
        doc = DocInfo(
            path="/tmp/memory/MEMORY.md", content=_INDEX_MD,
            doc_type="claude_memory", project_name="test-project",
        )
        result = ingest_claude_memory(ceo_db, mock_emb, doc, project_id="proj-123")
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_dedup_prevents_double_insert(self, ceo_db, mock_emb):
        doc = DocInfo(
            path="/tmp/feedback_test.md", content=_FEEDBACK_MD,
            doc_type="claude_memory", project_name="test-project",
        )
        r1 = ingest_claude_memory(ceo_db, mock_emb, doc, project_id="proj-123")
        r2 = ingest_claude_memory(ceo_db, mock_emb, doc, project_id="proj-123")
        assert r1["imported"] == 1
        assert r2["skipped"] == 1  # dedup should prevent re-insert


# ---------------------------------------------------------------------------
# dedup_fact
# ---------------------------------------------------------------------------

class TestDedupFact:
    def test_new_fact_is_add(self, ceo_db, mock_emb):
        f = Fact(content="The server runs on port 9527", category="infrastructure")
        f.embedding = mock_emb.encode_document(f.content)
        result = dedup_fact(ceo_db, mock_emb, f)
        assert result.action == "ADD"

    def test_duplicate_fact_is_noop(self, ceo_db, mock_emb):
        f1 = Fact(content="The server runs on port 9527", category="infrastructure")
        f1.embedding = mock_emb.encode_document(f1.content)
        ceo_db.insert_fact(f1)

        f2 = Fact(content="The server runs on port 9527", category="infrastructure")
        f2.embedding = mock_emb.encode_document(f2.content)
        result = dedup_fact(ceo_db, mock_emb, f2)
        assert result.action == "NOOP"

    def test_no_embedding_is_add(self, ceo_db, mock_emb):
        f = Fact(content="Some fact without embedding")
        result = dedup_fact(ceo_db, mock_emb, f)
        assert result.action == "ADD"


# ---------------------------------------------------------------------------
# sync_project_memories (end-to-end with tmp dir)
# ---------------------------------------------------------------------------

class TestSyncProjectMemories:
    def _setup_memory_dir(self, tmp_path, cwd: str):
        """Create a fake Claude projects memory dir matching the cwd."""
        encoded = cwd.replace("/", "-")
        memory_dir = tmp_path / encoded / "memory"
        memory_dir.mkdir(parents=True)
        return memory_dir

    def test_sync_end_to_end(self, ceo_db, mock_emb, tmp_path, monkeypatch):
        cwd = "/tmp/fake-project"
        memory_dir = self._setup_memory_dir(tmp_path, cwd)

        # Write a memory file
        (memory_dir / "feedback_test.md").write_text(_FEEDBACK_MD)
        (memory_dir / "MEMORY.md").write_text(_INDEX_MD)

        # Patch _CLAUDE_PROJECTS_DIR
        monkeypatch.setattr(
            "memory_core.import_agent._CLAUDE_PROJECTS_DIR", str(tmp_path)
        )
        # Patch detect_project to return a fixed id
        monkeypatch.setattr(
            "memory_core.project_detect.detect_project",
            lambda *a, **kw: "test-proj-id",
        )

        state = ImportState(str(tmp_path / "state.json"))
        result = sync_project_memories(ceo_db, mock_emb, cwd, state=state)
        assert result["synced"] == 1
        assert result["skipped"] == 0

    def test_incremental_skip(self, ceo_db, mock_emb, tmp_path, monkeypatch):
        cwd = "/tmp/fake-project"
        memory_dir = self._setup_memory_dir(tmp_path, cwd)
        (memory_dir / "feedback_test.md").write_text(_FEEDBACK_MD)

        monkeypatch.setattr(
            "memory_core.import_agent._CLAUDE_PROJECTS_DIR", str(tmp_path)
        )
        monkeypatch.setattr(
            "memory_core.project_detect.detect_project",
            lambda *a, **kw: "test-proj-id",
        )

        state = ImportState(str(tmp_path / "state.json"))

        # First sync
        r1 = sync_project_memories(ceo_db, mock_emb, cwd, state=state)
        assert r1["synced"] == 1

        # Second sync — file unchanged, should skip
        r2 = sync_project_memories(ceo_db, mock_emb, cwd, state=state)
        assert r2["synced"] == 0
        assert r2["skipped"] >= 1

    def test_modified_file_resynced(self, ceo_db, mock_emb, tmp_path, monkeypatch):
        cwd = "/tmp/fake-project"
        memory_dir = self._setup_memory_dir(tmp_path, cwd)
        fpath = memory_dir / "feedback_test.md"
        fpath.write_text(_FEEDBACK_MD)

        monkeypatch.setattr(
            "memory_core.import_agent._CLAUDE_PROJECTS_DIR", str(tmp_path)
        )
        monkeypatch.setattr(
            "memory_core.project_detect.detect_project",
            lambda *a, **kw: "test-proj-id",
        )

        state = ImportState(str(tmp_path / "state.json"))

        # First sync
        sync_project_memories(ceo_db, mock_emb, cwd, state=state)

        # Modify the file (bump mtime)
        time.sleep(0.05)
        fpath.write_text(_USER_MD)

        # Second sync — file changed, should re-sync
        r2 = sync_project_memories(ceo_db, mock_emb, cwd, state=state)
        assert r2["synced"] == 1


# ---------------------------------------------------------------------------
# sync_single_memory_file
# ---------------------------------------------------------------------------

class TestSyncSingleFile:
    def test_syncs_single_file(self, ceo_db, mock_emb, tmp_path, monkeypatch):
        cwd = "/tmp/fake-project"
        fpath = tmp_path / "feedback_test.md"
        fpath.write_text(_FEEDBACK_MD)

        monkeypatch.setattr(
            "memory_core.project_detect.detect_project",
            lambda *a, **kw: "test-proj-id",
        )

        state = ImportState(str(tmp_path / "state.json"))
        result = sync_single_memory_file(
            ceo_db, mock_emb, str(fpath), cwd, state=state,
        )
        assert result["synced"] == 1

    def test_skips_memory_md(self, ceo_db, mock_emb, tmp_path, monkeypatch):
        cwd = "/tmp/fake-project"
        fpath = tmp_path / "MEMORY.md"
        fpath.write_text(_INDEX_MD)

        state = ImportState(str(tmp_path / "state.json"))
        result = sync_single_memory_file(
            ceo_db, mock_emb, str(fpath), cwd, state=state,
        )
        assert result["skipped"] == 1
