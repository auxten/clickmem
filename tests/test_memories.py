"""Memory CRUD: Expand / Revise / Contract / Pin / Refuse + history append."""

from __future__ import annotations

import pytest

from clickmem import memories
from clickmem.history import get_history


def test_expand_returns_added_status(backend):
    res = memories.add("first principle of clean code", kind="principle", project_id="p1")
    assert res["status"] == "added"
    assert res["id"]
    got = memories.get(res["id"])
    assert got is not None
    assert got.content == "first principle of clean code"
    assert got.kind == "principle"
    assert got.project_id == "p1"
    assert got.status == "active"


def test_expand_appends_history_row(backend):
    res = memories.add("history entrypoint", kind="fact", project_id="p1")
    hist = get_history(res["id"])
    assert len(hist) == 1
    assert hist[0].op == "expand"
    assert hist[0].version == 1
    assert hist[0].content == "history entrypoint"


def test_revise_changes_content_and_appends_history(backend):
    res = memories.add("draft sentence", kind="fact", project_id="p1")
    out = memories.edit(res["id"], content="finalised sentence", agent="tester")
    assert out["status"] == "edited"
    got = memories.get(res["id"])
    assert got is not None
    assert got.content == "finalised sentence"
    hist = get_history(res["id"])
    assert [h.op for h in hist] == ["expand", "revise"]
    assert hist[-1].version == 2


def test_contract_marks_contracted_and_excludes_from_list(backend):
    res = memories.add("forget me", kind="free", project_id="p1")
    out = memories.forget(res["id"], reason="obsolete")
    assert out["status"] == "contracted"

    got = memories.get(res["id"])
    assert got is not None
    assert got.status == "contracted"
    assert got.contract_reason == "obsolete"

    hist = get_history(res["id"])
    assert [h.op for h in hist][-1] == "contract"


def test_pin_and_unpin(backend):
    res = memories.add("important rule", kind="principle", project_id="p1")
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


def test_add_with_blacklist_pattern_is_refused(backend):
    from clickmem import blacklist as bl
    bl.add("secret-pattern", scope="global", reason="test")
    res = memories.add("this contains secret-pattern in it", kind="free", project_id="p1")
    assert res["status"] == "refused"


def test_bulk_pin_unpin(backend):
    ids = [memories.add(f"item {i}", project_id="p1")["id"] for i in range(3)]
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
    memories.add("alpha", kind="principle", project_id="p1")
    memories.add("beta", kind="fact", project_id="p2")
    memories.add("gamma", kind="fact", project_id="p2")
    listing = memories.list_paginated(project_id="p2")
    assert listing["total"] == 2
    listing = memories.list_paginated(kind="principle")
    assert listing["total"] == 1
    listing = memories.list_paginated(search="amm")  # matches "gamma"
    assert listing["total"] == 1
