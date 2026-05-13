"""Raw transcript landing: dedup + never surfaced in recall."""

from __future__ import annotations

from clickmem import memories, raw
from clickmem.recall import recall


def test_append_then_dedup(backend):
    first = raw.append("hello world", session_id="s1", agent="claude_code")
    assert first["ok"] is True
    assert "id" in first

    dup = raw.append("hello world", session_id="s1", agent="claude_code")
    assert dup.get("skipped") == "duplicate"
    assert dup.get("id") == first["id"]


def test_append_empty_is_skipped(backend):
    res = raw.append("   ", session_id="s2", agent="claude_code")
    assert res.get("skipped") == "empty"


def test_get_raw_filters_by_session(backend):
    raw.append("first message", session_id="s_a", agent="cursor")
    raw.append("second message", session_id="s_b", agent="cursor")
    rows = raw.get_raw(session_id="s_a")
    assert len(rows) == 1
    assert "first message" in rows[0]["text"]


def test_raw_never_surfaces_in_recall(backend):
    raw.append("this is raw text only", session_id="s3", agent="claude_code")
    # Recall should return empty because no memory rows exist.
    hits = recall("this is raw text only", project_id="", limit=5, cross_project=True)
    assert hits == []
    # Add a real memory so we can confirm recall does see memories table.
    memories.add("this is a real memory entry", project_id="p1", privacy="public")
    hits = recall("real memory entry", project_id="p1", limit=5)
    assert any("real memory" in h.content for h in hits)
