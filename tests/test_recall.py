"""Recall scoring: project boost, privacy filter, blacklist filter, pinned boost."""

from __future__ import annotations

from clickmem import memories
from clickmem.recall import recall, recall_trace


def test_same_project_beats_global_and_other_project(backend):
    same = memories.add("alpha bravo charlie", project_id="p1", privacy="public", tags=["test"])["id"]
    glob = memories.add("alpha bravo charlie", project_id="global", privacy="public", tags=["test"])["id"]
    other = memories.add("alpha bravo charlie", project_id="p2", privacy="public", tags=["test"])["id"]

    hits = recall("alpha bravo charlie", project_id="p1", limit=5)
    ids = [h.id for h in hits]

    assert same in ids
    assert glob in ids
    assert other not in ids

    same_hit = next(h for h in hits if h.id == same)
    glob_hit = next(h for h in hits if h.id == glob)
    assert same_hit.project_boost == 1.0
    assert glob_hit.project_boost == 0.9
    assert same_hit.score > glob_hit.score


def test_cross_project_includes_other_project(backend):
    memories.add("zeta payload one", project_id="p1", privacy="public", tags=["test"])
    memories.add("zeta payload two", project_id="p2", privacy="public", tags=["test"])

    hits = recall("zeta payload", project_id="p1", cross_project=True, limit=10)
    pids = {h.project_id for h in hits}
    assert {"p1", "p2"}.issubset(pids)


def test_privacy_filter_blocks_confidential_by_default(backend):
    pub = memories.add("nonsecret message body", project_id="p1", privacy="public", tags=["test"])["id"]
    conf = memories.add("secret confidential message body", project_id="p1", privacy="confidential", tags=["test"])["id"]
    assert pub != conf

    hits = recall("nonsecret message body", project_id="p1", limit=10)
    ids = [h.id for h in hits]
    assert pub in ids
    assert conf not in ids

    hits = recall("secret confidential message body", project_id="p1", include_confidential=True, limit=10)
    ids = [h.id for h in hits]
    assert conf in ids


def test_blacklist_filter_drops_hits(backend):
    from clickmem import blacklist as bl

    mid = memories.add("forbidden topic discussion", project_id="p1", privacy="public", tags=["test"])["id"]
    bl.add("forbidden topic", scope="global", reason="testing")

    hits = recall("forbidden topic discussion", project_id="p1", limit=10)
    assert all(h.id != mid for h in hits)


def test_pinned_short_circuits_to_top(backend):
    a = memories.add("ranking probe alpha", project_id="p1", privacy="public", tags=["test"])["id"]
    b = memories.add("ranking probe beta", project_id="p1", privacy="public", tags=["test"])["id"]
    memories.pin(b)

    hits = recall("ranking probe alpha", project_id="p1", limit=5)
    assert hits, "expected at least one hit"
    assert hits[0].id == b
    assert hits[0].pinned is True


def test_recall_trace_breakdown(backend):
    a = memories.add("alpha source one", project_id="p1", privacy="public", tags=["test"])["id"]
    b = memories.add("alpha source two", project_id="p2", privacy="public", tags=["test"])["id"]

    trace = recall_trace("alpha source", project_id="p1", cross_project=False, limit=5)
    assert trace["query"] == "alpha source"
    cand_ids = {c["id"] for c in trace["candidates"]}
    assert a in cand_ids
    assert b not in cand_ids

    cross_trace = recall_trace("alpha source", project_id="p1", cross_project=True, limit=5)
    cross_ids = {c["id"] for c in cross_trace["candidates"]}
    assert b in cross_ids
    cand_b = next(c for c in cross_trace["candidates"] if c["id"] == b)
    assert cand_b["project_boost"] == 1.0
    assert cand_b["kept"] is True

    cand_a = next(c for c in trace["candidates"] if c["id"] == a)
    assert cand_a["kept"] is True
    assert cand_a["project_boost"] == 1.0


def test_recall_tags_filter_and_boost(backend):
    a = memories.add("deploy local mini workflow", project_id="p1", privacy="public", tags=["deployment", "workflow"])["id"]
    b = memories.add("deploy local mini security", project_id="p1", privacy="public", tags=["deployment", "security"])["id"]

    hits = recall("deploy local mini", project_id="p1", tags=["deployment", "workflow"], tag_mode="all", limit=5)
    ids = [h.id for h in hits]
    assert a in ids
    assert b not in ids
    hit = next(h for h in hits if h.id == a)
    assert hit.tag_match_count == 2
    assert hit.tag_boost > 1.0

    trace = recall_trace("deploy local mini", project_id="p1", tags=["deployment"], limit=5)
    assert trace["filters"]["tags"] == ["deployment"]
    assert trace["candidates"]
    assert all(c["tag_match_count"] >= 1 for c in trace["candidates"])


def test_recall_empty_query_returns_empty(backend):
    memories.add("anything", project_id="p1", tags=["test"])
    assert recall("", project_id="p1") == []
    assert recall("   ", project_id="p1") == []
