"""Tests for Markdown export (md_sync).

Covers export_memory_md (L2 -> MEMORY.md) and export_daily_md (L1 -> daily .md).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from memory_core import MemoryDB, md_sync
from memory_core.models import Memory
from tests.helpers.factories import make_memory, seed_semantic, seed_episodic


class TestExportMemoryMd:
    """Test md_sync.export_memory_md — L2 -> MEMORY.md."""

    def test_creates_file(self, populated_db, workspace_path):
        """export_memory_md creates a MEMORY.md file."""
        path = md_sync.export_memory_md(populated_db, workspace_path)
        assert os.path.exists(path)

    def test_returns_file_path(self, populated_db, workspace_path):
        """export_memory_md returns the path to the created file."""
        path = md_sync.export_memory_md(populated_db, workspace_path)
        assert path.endswith("MEMORY.md")

    def test_contains_semantic_content(self, populated_db, workspace_path):
        """MEMORY.md contains semantic memory content."""
        path = md_sync.export_memory_md(populated_db, workspace_path)
        with open(path) as f:
            content = f.read()
        assert "Prefers SwiftUI over UIKit" in content

    def test_excludes_episodic(self, populated_db, workspace_path):
        """MEMORY.md should not contain episodic-only content."""
        path = md_sync.export_memory_md(populated_db, workspace_path)
        with open(path) as f:
            content = f.read()
        # Episodic content from seed data should not appear
        assert "Project kickoff, goal: replace sqlite-vec" not in content

    def test_empty_db_creates_empty_file(self, db, workspace_path):
        """export_memory_md creates a file even if no semantic memories exist."""
        path = md_sync.export_memory_md(db, workspace_path)
        assert os.path.exists(path)

    def test_groups_by_category(self, populated_db, workspace_path):
        """MEMORY.md organizes entries by category."""
        path = md_sync.export_memory_md(populated_db, workspace_path)
        with open(path) as f:
            content = f.read()
        # Should have category headers
        assert "preference" in content.lower() or "knowledge" in content.lower()

    def test_overwrites_existing_file(self, populated_db, workspace_path):
        """Calling export_memory_md twice overwrites the file."""
        path1 = md_sync.export_memory_md(populated_db, workspace_path)
        with open(path1) as f:
            content1 = f.read()
        path2 = md_sync.export_memory_md(populated_db, workspace_path)
        assert path1 == path2
        with open(path2) as f:
            content2 = f.read()
        assert content1 == content2


class TestExportDailyMd:
    """Test md_sync.export_daily_md — L1 -> daily .md files."""

    def test_creates_file(self, populated_db, workspace_path):
        """export_daily_md creates a daily markdown file."""
        path = md_sync.export_daily_md(populated_db, workspace_path)
        assert os.path.exists(path)

    def test_returns_file_path(self, populated_db, workspace_path):
        """export_daily_md returns the path to the daily file."""
        path = md_sync.export_daily_md(populated_db, workspace_path)
        assert "memory/" in path or "memory" in os.path.dirname(path)
        assert path.endswith(".md")

    def test_contains_episodic_content(self, db, workspace_path, mock_emb):
        """Daily .md contains today's episodic events."""
        now = datetime.now(timezone.utc)
        m = make_memory(
            layer="episodic",
            content="Today's important decision",
            created_at=now,
            embedding=mock_emb.encode_document("Today's important decision"),
        )
        db.insert(m)
        path = md_sync.export_daily_md(db, workspace_path)
        with open(path) as f:
            content = f.read()
        assert "Today's important decision" in content

    def test_specific_date(self, db, workspace_path, mock_emb):
        """export_daily_md can export a specific date."""
        m = make_memory(
            layer="episodic",
            content="March 1st event",
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            embedding=mock_emb.encode_document("March 1st event"),
        )
        db.insert(m)
        path = md_sync.export_daily_md(db, workspace_path, date="2026-03-01")
        assert "2026-03-01" in path
        with open(path) as f:
            content = f.read()
        assert "March 1st event" in content

    def test_empty_day_creates_file(self, db, workspace_path):
        """export_daily_md creates a file even for a day with no events."""
        path = md_sync.export_daily_md(db, workspace_path, date="2026-06-15")
        assert os.path.exists(path)

    def test_creates_memory_subdirectory(self, db, workspace_path):
        """export_daily_md creates the memory/ subdirectory if needed."""
        path = md_sync.export_daily_md(db, workspace_path, date="2026-03-04")
        memory_dir = os.path.join(workspace_path, "memory")
        assert os.path.isdir(memory_dir)
