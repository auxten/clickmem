"""memory_history append + version chain."""

from __future__ import annotations

from clickmem import memories
from clickmem.history import diff, get_history, history_with_diffs


def test_expand_creates_v1(backend):
    res = memories.add("v1 contents", project_id="p1", tags=["test"])
    hist = get_history(res["id"])
    assert [h.version for h in hist] == [1]
    assert hist[0].op == "expand"


def test_revise_chain(backend):
    res = memories.add("v1 contents", project_id="p1", tags=["test"])
    memories.edit(res["id"], content="v2 contents")
    memories.edit(res["id"], content="v3 contents")

    hist = get_history(res["id"])
    versions = [(h.version, h.op, h.content) for h in hist]
    assert versions == [
        (1, "expand", "v1 contents"),
        (2, "revise", "v2 contents"),
        (3, "revise", "v3 contents"),
    ]


def test_contract_and_pin_append_history(backend):
    res = memories.add("history-rich", project_id="p1", tags=["test"])
    memories.pin(res["id"])
    memories.forget(res["id"], reason="done")

    ops = [h.op for h in get_history(res["id"])]
    assert ops == ["expand", "pin", "contract"]


def test_history_with_diffs_emits_unified_diff(backend):
    res = memories.add("alpha\nbravo", project_id="p1", tags=["test"])
    memories.edit(res["id"], content="alpha\ncharlie")
    out = history_with_diffs(res["id"])
    assert len(out) == 2
    assert out[0]["diff"] == []
    assert any("bravo" in line or "charlie" in line for line in out[1]["diff"])


def test_diff_helper_unified_form():
    out = diff("a\nb\nc", "a\nB\nc")
    joined = "\n".join(out)
    assert "-b" in joined and "+B" in joined
