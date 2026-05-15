"""Memory CRUD: Expand / Revise / Contract / Pin / Refuse + history append."""

from __future__ import annotations

import pytest

from clickmem import memories
from clickmem.embedding import set_embedder
from clickmem.history import get_history


class _ExplodingEmbedder:
    def encode(self, text: str):  # noqa: ANN201
        raise AssertionError("queue_add must not embed on the request path")

    def encode_batch(self, texts):  # noqa: ANN001, ANN201
        raise AssertionError("queue_add must not embed on the request path")


class _MergeEmbedder:
    def __init__(self, dim: int = 256):
        self.dim = dim

    def encode(self, text: str):  # noqa: ANN201
        vec = [0.0] * self.dim
        vec[0] = 1.0
        return vec

    def encode_batch(self, texts):  # noqa: ANN001, ANN201
        return [self.encode(t) for t in texts]


def test_expand_returns_added_status(backend):
    res = memories.add("first principle of clean code", kind="principle", project_id="p1", tags=["test"])
    assert res["status"] == "added"
    assert res["id"]
    got = memories.get(res["id"])
    assert got is not None
    assert got.content == "first principle of clean code"
    assert got.kind == "principle"
    assert got.project_id == "p1"
    assert got.status == "active"


def test_expand_appends_history_row(backend):
    res = memories.add("history entrypoint", kind="fact", project_id="p1", tags=["test"])
    hist = get_history(res["id"])
    assert len(hist) == 1
    assert hist[0].op == "expand"
    assert hist[0].version == 1
    assert hist[0].content == "history entrypoint"


def test_revise_changes_content_and_appends_history(backend):
    res = memories.add("draft sentence", kind="fact", project_id="p1", tags=["test"])
    out = memories.edit(res["id"], content="finalised sentence", agent="tester")
    assert out["status"] == "edited"
    got = memories.get(res["id"])
    assert got is not None
    assert got.content == "finalised sentence"
    hist = get_history(res["id"])
    assert [h.op for h in hist] == ["expand", "revise"]
    assert hist[-1].version == 2


def test_edit_project_and_kind_replaces_single_memory_row(backend):
    res = memories.add("move me", kind="fact", project_id="p1", tags=["test"])

    out = memories.edit(res["id"], project_id="p2", kind="decision", agent="tester")

    assert out["status"] == "edited"
    rows = backend.query(f"SELECT id, project_id, kind FROM memories FINAL WHERE id = '{res['id']}'")
    assert rows == [{"id": res["id"], "project_id": "p2", "kind": "decision"}]
    assert memories.list_paginated(project_id="p1")["total"] == 0
    assert memories.list_paginated(project_id="p2")["total"] == 1


def test_contract_marks_contracted_and_excludes_from_list(backend):
    res = memories.add("forget me", kind="free", project_id="p1", tags=["test"])
    out = memories.forget(res["id"], reason="obsolete")
    assert out["status"] == "contracted"

    got = memories.get(res["id"])
    assert got is not None
    assert got.status == "contracted"
    assert got.contract_reason == "obsolete"

    hist = get_history(res["id"])
    assert [h.op for h in hist][-1] == "contract"


def test_pin_and_unpin(backend):
    res = memories.add("important rule", kind="principle", project_id="p1", tags=["test"])
    memories.pin(res["id"])
    got = memories.get(res["id"])
    assert got is not None
    assert got.pinned is True

    memories.unpin(res["id"])
    got = memories.get(res["id"])
    assert got is not None
    assert got.pinned is False


def test_add_blank_content_raises(backend):
    with pytest.raises(ValueError):
        memories.add("   ", kind="free", project_id="p1")


def test_add_requires_explicit_scope_and_tags(backend):
    with pytest.raises(ValueError, match="explicit scope and tags"):
        memories.add("missing project", kind="free", tags=["test"])
    with pytest.raises(ValueError, match="explicit scope and tags"):
        memories.add("missing tags", kind="free", project_id="p1")


def test_add_accepts_explicit_global_scope(backend):
    res = memories.add("global principle", kind="principle", project_id="global", tags=["test"])
    got = memories.get(res["id"])
    assert got is not None
    assert got.project_id == ""
    assert got.tags == ["test"]


def test_add_with_blacklist_pattern_is_refused(backend):
    from clickmem import blacklist as bl
    bl.add("secret-pattern", scope="global", reason="test")
    res = memories.add("this contains secret-pattern in it", kind="free", project_id="p1", tags=["test"])
    assert res["status"] == "refused"


def test_queue_add_does_not_embed_on_request_path(backend):
    set_embedder(_ExplodingEmbedder())

    res = memories.queue_add("queued without embedding", kind="fact", project_id="p1", tags=["test"])

    assert res["status"] == "queued"
    got = memories.get(res["id"])
    assert got is not None
    assert got.pending_embedding is True
    assert got.embedding == []


def test_process_pending_embeddings_finalizes_queued_memory(backend):
    res = memories.queue_add("queued finalization", kind="fact", project_id="p1", tags=["test"])
    got = memories.get(res["id"])
    assert got is not None and got.pending_embedding is True

    out = memories.process_pending_embeddings()

    assert out["processed"] == 1
    got = memories.get(res["id"])
    assert got is not None
    assert got.pending_embedding is False
    assert got.embed_attempts == 1
    assert got.embedding
    assert got.status == "active"


def test_process_pending_embeddings_merges_duplicate_after_embedding(backend):
    set_embedder(_MergeEmbedder())
    existing = memories.add("Build twice. Verify once.", kind="fact", project_id="p1", tags=["test"])
    queued = memories.queue_add("BUILD TWICE; verify once", kind="fact", project_id="p1", tags=["test"])

    out = memories.process_pending_embeddings()

    assert out["processed"] == 1
    assert out["results"]["merged"] == 1
    assert memories.get(existing["id"]).status == "active"
    queued_row = memories.get(queued["id"])
    assert queued_row is not None
    assert queued_row.status == "contracted"
    assert queued_row.pending_embedding is False


def test_bulk_pin_unpin(backend):
    ids = [memories.add(f"item {i}", project_id="p1", tags=["test"])["id"] for i in range(3)]
    result = memories.bulk(ids, "pin")
    assert result["count"] == 3
    for mid in ids:
        m = memories.get(mid)
        assert m is not None and m.pinned is True

    memories.bulk(ids, "unpin")
    for mid in ids:
        m = memories.get(mid)
        assert m is not None and m.pinned is False


def test_list_paginated_filters(backend):
    memories.add("alpha", kind="principle", project_id="p1", tags=["test"])
    memories.add("beta", kind="fact", project_id="p2", tags=["test"])
    memories.add("gamma", kind="fact", project_id="p2", tags=["test"])
    listing = memories.list_paginated(project_id="p2")
    assert listing["total"] == 2
    listing = memories.list_paginated(kind="principle")
    assert listing["total"] == 1
    listing = memories.list_paginated(search="amm")  # matches "gamma"
    assert listing["total"] == 1
