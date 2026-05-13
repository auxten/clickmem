"""Portable export + import: JSONL round-trip + Markdown layout + dedup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clickmem import memories, portable
from clickmem.backend import reset_backend


def _seed():
    a = memories.add("alpha export row", project_id="p1", privacy="public")["id"]
    b = memories.add("bravo export row", project_id="p2", privacy="private")["id"]
    return a, b


def test_jsonl_round_trip_preserves_embeddings(tmp_path, backend):
    _seed()
    out = tmp_path / "export.jsonl"
    res = portable.export_jsonl(out)
    assert res["ok"]
    assert res["count"] == 2

    with open(out, encoding="utf-8") as fh:
        lines = [json.loads(ln) for ln in fh if ln.strip()]
    assert lines[0].get("clickmem_export") == "1.0"
    row = lines[1]
    assert "embedding" in row and len(row["embedding"]) == 256


def test_markdown_export_structure(tmp_path, backend):
    a, b = _seed()
    out = tmp_path / "export.md"
    res = portable.export_markdown(out)
    assert res["ok"]
    text = out.read_text(encoding="utf-8")
    assert "# ClickMem export" in text
    assert f"## {a}" in text
    assert f"## {b}" in text
    assert "kind:" in text and "project:" in text


def test_jsonl_import_dedups_existing(tmp_path, backend, monkeypatch):
    """Re-importing a bundle into a DB that already has the same content_hash
    skips those rows."""
    _seed()
    out = tmp_path / "export.jsonl"
    portable.export_jsonl(out)

    # Re-import into the same DB — every row is a content_hash duplicate.
    res = portable.import_jsonl(out)
    assert res["ok"]
    assert res["skipped"] == 2
    assert res["ingested"] == 0


def test_jsonl_import_into_empty_db_inserts(tmp_path, backend, monkeypatch):
    """Round-trip: export → wipe → import → rows re-appear."""
    _seed()
    out = tmp_path / "export.jsonl"
    portable.export_jsonl(out)
    # Hard wipe of memories so the next import inserts everything.
    backend.execute("TRUNCATE TABLE memories")
    res = portable.import_jsonl(out)
    assert res["ingested"] == 2
    assert res["skipped"] == 0


def test_import_missing_file_returns_error(tmp_path, backend):
    res = portable.import_jsonl(tmp_path / "nope.jsonl")
    assert res["ok"] is False
    assert "not found" in res.get("error", "").lower()
