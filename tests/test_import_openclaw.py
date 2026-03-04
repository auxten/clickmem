"""Tests for OpenClaw import/export functionality."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
from typer.testing import CliRunner

from memory_core.cli import app
from memory_core.import_openclaw import (
    import_workspace_memories,
    import_sqlite_chunks,
    _content_exists,
    _read_file,
    _read_sqlite_chunks,
)

runner = CliRunner()


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def openclaw_dir(tmp_path):
    """Create a fake ~/.openclaw directory structure."""
    oc = tmp_path / ".openclaw"
    oc.mkdir()
    return str(oc)


@pytest.fixture
def openclaw_with_workspace(openclaw_dir):
    """OpenClaw dir with workspace memory .md files."""
    ws = os.path.join(openclaw_dir, "workspace-abc123", "memory")
    os.makedirs(ws)

    # Date-named file
    with open(os.path.join(ws, "2026-03-01.md"), "w") as f:
        f.write("Discussed API design with team, decided on REST over gRPC")

    with open(os.path.join(ws, "2026-03-02.md"), "w") as f:
        f.write("Fixed authentication bug in login flow")

    # Non-date file
    with open(os.path.join(ws, "notes.md"), "w") as f:
        f.write("General project notes about architecture")

    # Empty file (should be skipped)
    with open(os.path.join(ws, "empty.md"), "w") as f:
        f.write("")

    return openclaw_dir


@pytest.fixture
def openclaw_with_sqlite(openclaw_dir):
    """OpenClaw dir with memory SQLite databases."""
    mem_dir = os.path.join(openclaw_dir, "memory")
    os.makedirs(mem_dir)

    db_path = os.path.join(mem_dir, "chunks.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO chunks (text) VALUES ('User prefers dark mode')")
    conn.execute("INSERT INTO chunks (text) VALUES ('Project uses Python 3.12')")
    conn.execute("INSERT INTO chunks (text) VALUES ('')")  # empty, should skip
    conn.commit()
    conn.close()

    return openclaw_dir


@pytest.fixture
def openclaw_full(openclaw_with_workspace):
    """OpenClaw dir with both workspace .md and SQLite data."""
    mem_dir = os.path.join(openclaw_with_workspace, "memory")
    os.makedirs(mem_dir, exist_ok=True)

    db_path = os.path.join(mem_dir, "vectors.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, content TEXT)")
    conn.execute("INSERT INTO data (content) VALUES ('Semantic fact from SQLite')")
    conn.commit()
    conn.close()

    return openclaw_with_workspace


# ── Unit tests: _read_file ───────────────────────────────────────────


class TestReadFile:
    def test_reads_utf8(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("hello world")
        assert _read_file(str(f)) == "hello world"

    def test_strips_whitespace(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("  hello  \n\n")
        assert _read_file(str(f)) == "hello"

    def test_missing_file(self):
        assert _read_file("/nonexistent/path.md") == ""

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("")
        assert _read_file(str(f)) == ""


# ── Unit tests: _read_sqlite_chunks ──────────────────────────────────


class TestReadSqliteChunks:
    def test_reads_text_column(self, tmp_path):
        db_path = str(tmp_path / "test.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE chunks (text TEXT)")
        conn.execute("INSERT INTO chunks (text) VALUES ('chunk one')")
        conn.execute("INSERT INTO chunks (text) VALUES ('chunk two')")
        conn.commit()
        conn.close()

        chunks = _read_sqlite_chunks(db_path)
        assert set(chunks) == {"chunk one", "chunk two"}

    def test_reads_content_column(self, tmp_path):
        db_path = str(tmp_path / "test.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE data (content TEXT)")
        conn.execute("INSERT INTO data (content) VALUES ('data one')")
        conn.commit()
        conn.close()

        chunks = _read_sqlite_chunks(db_path)
        assert chunks == ["data one"]

    def test_empty_database(self, tmp_path):
        db_path = str(tmp_path / "empty.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()
        conn.close()

        chunks = _read_sqlite_chunks(db_path)
        assert chunks == []


# ── Unit tests: _content_exists ──────────────────────────────────────


class TestContentExists:
    def test_no_match(self, db):
        assert not _content_exists(db, "nonexistent content")

    def test_match_found(self, db, mock_emb):
        from memory_core.models import Memory

        m = Memory(content="existing content", embedding=mock_emb.encode_document("existing content"))
        db.insert(m)
        assert _content_exists(db, "existing content")

    def test_inactive_not_matched(self, db, mock_emb):
        from memory_core.models import Memory

        m = Memory(content="deactivated", embedding=mock_emb.encode_document("deactivated"))
        db.insert(m)
        db.deactivate(m.id)
        assert not _content_exists(db, "deactivated")


# ── Integration tests: import_workspace_memories ─────────────────────


class TestImportWorkspaceMemories:
    def test_imports_md_files(self, db, mock_emb, openclaw_with_workspace):
        result = import_workspace_memories(db, mock_emb, openclaw_with_workspace)
        assert result["imported"] == 3  # 2 dated + 1 notes, empty skipped
        assert result["skipped"] == 1  # empty file

    def test_imported_as_episodic(self, db, mock_emb, openclaw_with_workspace):
        import_workspace_memories(db, mock_emb, openclaw_with_workspace)
        memories = db.list_by_layer("episodic")
        assert len(memories) == 3
        for m in memories:
            assert m.source == "openclaw_import"
            assert "openclaw_import" in m.tags

    def test_date_tag_extracted(self, db, mock_emb, openclaw_with_workspace):
        import_workspace_memories(db, mock_emb, openclaw_with_workspace)
        memories = db.list_by_layer("episodic")
        date_tags = set()
        for m in memories:
            for t in m.tags:
                if t.startswith("2026-"):
                    date_tags.add(t)
        assert "2026-03-01" in date_tags
        assert "2026-03-02" in date_tags

    def test_no_duplicates_on_reimport(self, db, mock_emb, openclaw_with_workspace):
        r1 = import_workspace_memories(db, mock_emb, openclaw_with_workspace)
        r2 = import_workspace_memories(db, mock_emb, openclaw_with_workspace)
        assert r1["imported"] == 3
        assert r2["imported"] == 0
        assert r2["skipped"] == 4  # 3 already exist + 1 empty

    def test_empty_dir(self, db, mock_emb, openclaw_dir):
        result = import_workspace_memories(db, mock_emb, openclaw_dir)
        assert result["imported"] == 0
        assert result["skipped"] == 0

    def test_embeddings_generated(self, db, mock_emb, openclaw_with_workspace):
        import_workspace_memories(db, mock_emb, openclaw_with_workspace)
        memories = db.list_by_layer("episodic")
        for m in memories:
            assert m.embedding is not None
            assert len(m.embedding) == 256


# ── Integration tests: import_sqlite_chunks ──────────────────────────


class TestImportSqliteChunks:
    def test_imports_chunks(self, db, mock_emb, openclaw_with_sqlite):
        result = import_sqlite_chunks(db, mock_emb, openclaw_with_sqlite)
        assert result["imported"] == 2  # 2 non-empty chunks
        assert result["skipped"] == 0  # empty string filtered at sqlite read level

    def test_imported_as_semantic(self, db, mock_emb, openclaw_with_sqlite):
        import_sqlite_chunks(db, mock_emb, openclaw_with_sqlite)
        memories = db.list_by_layer("semantic")
        assert len(memories) == 2
        for m in memories:
            assert m.source == "openclaw_import"
            assert m.category == "knowledge"

    def test_no_duplicates_on_reimport(self, db, mock_emb, openclaw_with_sqlite):
        r1 = import_sqlite_chunks(db, mock_emb, openclaw_with_sqlite)
        r2 = import_sqlite_chunks(db, mock_emb, openclaw_with_sqlite)
        assert r1["imported"] == 2
        assert r2["imported"] == 0

    def test_empty_memory_dir(self, db, mock_emb, openclaw_dir):
        result = import_sqlite_chunks(db, mock_emb, openclaw_dir)
        assert result["imported"] == 0
        assert result["skipped"] == 0


# ── CLI tests: import-openclaw ───────────────────────────────────────


def _extract_json(output: str) -> dict:
    """Extract the last JSON object from CLI output (may contain warnings/progress bars)."""
    # Find the last '{' ... '}' pair in the output
    idx = output.rfind("}")
    if idx == -1:
        raise ValueError(f"No JSON found in output: {output!r}")
    # Walk backwards to find the matching '{'
    depth = 0
    for i in range(idx, -1, -1):
        if output[i] == "}":
            depth += 1
        elif output[i] == "{":
            depth -= 1
        if depth == 0:
            return json.loads(output[i : idx + 1])
    raise ValueError(f"No JSON found in output: {output!r}")


class TestImportOpenclawCLI:
    def test_json_output(self, openclaw_full):
        result = runner.invoke(app, ["import-openclaw", "--openclaw-dir", openclaw_full, "--json"])
        assert result.exit_code == 0
        data = _extract_json(result.output)
        assert "total_imported" in data
        assert "workspace_memories" in data
        assert "sqlite_chunks" in data

    def test_missing_dir(self):
        result = runner.invoke(app, ["import-openclaw", "--openclaw-dir", "/nonexistent/dir", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data

    def test_human_output(self, openclaw_full):
        result = runner.invoke(app, ["import-openclaw", "--openclaw-dir", openclaw_full])
        assert result.exit_code == 0
        assert "import complete" in result.output.lower()


# ── CLI tests: export-context ────────────────────────────────────────


class TestExportContextCLI:
    def test_json_output(self, tmp_path):
        ws = str(tmp_path / "export-ws")
        result = runner.invoke(app, ["export-context", ws, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "memory_md" in data
        assert "daily_md" in data

    def test_creates_files(self, tmp_path):
        ws = str(tmp_path / "export-ws")
        runner.invoke(app, ["export-context", ws])
        assert os.path.isfile(os.path.join(ws, "MEMORY.md"))
        assert os.path.isdir(os.path.join(ws, "memory"))

    def test_human_output(self, tmp_path):
        ws = str(tmp_path / "export-ws")
        result = runner.invoke(app, ["export-context", ws])
        assert result.exit_code == 0
        assert "Exported context" in result.output
