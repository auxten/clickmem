"""Blacklist: insert rejection + recall filter + pattern match types."""

from __future__ import annotations

from clickmem import blacklist as bl
from clickmem import memories
from clickmem.blacklist import enforce_on_insert, enforce_on_recall
from clickmem.recall import recall


def test_substring_pattern_rejects_insert(backend):
    bl.add("api-key", scope="global", reason="protect secrets")
    res = memories.add("we should rotate the API-KEY weekly", kind="fact", project_id="p1", tags=["test"])
    assert res["status"] == "refused"


def test_id_prefix_pattern_filters_recall_only(backend):
    mid = memories.add("benign content one", project_id="p1", privacy="public", tags=["test"])["id"]
    bl.add(f"id:{mid}", scope="global", reason="hide this row")

    hits = recall("benign content one", project_id="p1", limit=10)
    assert all(h.id != mid for h in hits)

    direct = enforce_on_insert("benign content one", project_id="p1")
    assert direct is None


def test_blacklist_scope_restricts_match(backend):
    bl.add("secret", scope="p2", reason="project specific")
    inserted = memories.add("secret data row", project_id="p1", privacy="public", tags=["test"])
    assert inserted["status"] == "added"


def test_list_and_remove(backend):
    entry = bl.add("trash", scope="global", reason="initial")
    assert any(b.id == entry.id for b in bl.list_all())
    bl.remove(entry.id)
    assert all(b.id != entry.id for b in bl.list_all())


def test_enforce_on_recall_increments_hit_count(backend):
    mid = memories.add("delete me hit count", project_id="p1", privacy="public", tags=["test"])["id"]
    entry = bl.add("delete me hit count", scope="global", reason="hit-count")

    fake_hit = {"id": mid, "content": "delete me hit count", "cosine_sim": 1.0}
    kept = enforce_on_recall([fake_hit], project_id="p1")
    assert kept == []
    refreshed = next(b for b in bl.list_all() if b.id == entry.id)
    assert refreshed.hit_count >= 1
