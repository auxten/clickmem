"""Conflict detection + resolution semantics."""

from __future__ import annotations

import pytest

from clickmem import conflicts, memories
from clickmem.embedding import set_embedder


class _SyntheticEmbed:
    """Make every text identical or completely orthogonal as the test asks."""

    def __init__(self, dim: int = 256, alias: str = "default"):
        self.dim = dim
        self.alias = alias

    def encode(self, text: str):
        text = (text or "").strip().lower()
        vec = [0.0] * self.dim
        if "alpha" in text:
            vec[0] = 1.0
        elif "beta" in text:
            vec[1] = 1.0
        elif "gamma" in text:
            vec[2] = 1.0
        else:
            vec[3] = 1.0
        return vec

    def encode_batch(self, texts):
        return [self.encode(t) for t in texts]


@pytest.fixture
def synthetic_embedder():
    """Replace the mock embedder with one that produces stable similar vectors."""
    set_embedder(_SyntheticEmbed())
    yield


def test_conflict_detected_when_embeddings_close(synthetic_embedder, backend):
    a = memories.add("alpha first variant text", kind="fact", project_id="p1")
    assert a["status"] == "added"
    b = memories.add("alpha second variant text", kind="fact", project_id="p1")
    assert b["status"] == "conflicted"
    assert a["id"] in b["peer_ids"]

    listing = conflicts.list_conflicts(project_id="p1")
    ids = {r["id"] for r in listing}
    assert a["id"] in ids and b["id"] in ids


def test_pinned_short_circuits_rejects_new_commit(synthetic_embedder, backend):
    a = memories.add("alpha pinned canonical", kind="fact", project_id="p1", pinned=True)
    assert a["status"] == "added"
    b = memories.add("alpha rival commit", kind="fact", project_id="p1")
    assert b["status"] == "rejected"
    assert a["id"] in b["peer_ids"]


def test_resolve_revise_contracts_peer(synthetic_embedder, backend):
    a = memories.add("alpha original", kind="fact", project_id="p1")
    b = memories.add("alpha variant", kind="fact", project_id="p1")
    assert b["status"] == "conflicted"

    res = conflicts.resolve(b["id"], "revise", peer_id=a["id"])
    assert res["status"] == "ok"
    assert memories.get(a["id"]).status == "contracted"
    assert memories.get(b["id"]).status == "active"


def test_resolve_contract_drops_peer_keeps_target(synthetic_embedder, backend):
    a = memories.add("alpha keep me", kind="fact", project_id="p1")
    b = memories.add("alpha drop me", kind="fact", project_id="p1")
    res = conflicts.resolve(a["id"], "contract", peer_id=b["id"])
    assert res["status"] == "ok"
    assert memories.get(a["id"]).status == "active"
    assert memories.get(b["id"]).status == "contracted"


def test_resolve_allow_clears_both(synthetic_embedder, backend):
    a = memories.add("alpha one", kind="fact", project_id="p1")
    b = memories.add("alpha two", kind="fact", project_id="p1")
    res = conflicts.resolve(a["id"], "allow", peer_id=b["id"])
    assert res["status"] == "ok"
    assert memories.get(a["id"]).status == "active"
    assert memories.get(b["id"]).status == "active"


def test_resolve_unknown_op_raises(backend):
    with pytest.raises(ValueError):
        conflicts.resolve("anything", "wat")


def test_canonical_merge_returns_same_id(synthetic_embedder, backend):
    """Two adds whose lower-cased / depunctuated form is identical merge.

    Uses the synthetic embedder so both texts hash to the same vector (they
    both share neither alpha/beta/gamma, so fall in the "else" bucket).
    """
    a = memories.add("Build twice. Verify once.", kind="fact", project_id="p1")
    b = memories.add("BUILD TWICE; verify once", kind="fact", project_id="p1")
    assert b["status"] == "merged"
    assert b["id"] == a["id"]
