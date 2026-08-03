"""Daily Pickup candidates stay inert until an explicit versioned decision."""

from __future__ import annotations

import hashlib

import pytest

from clickmem import pickup
from clickmem.recall import recall


def _session(
    *,
    source_hash: str = "hash-v1",
    content: str = "Use the release tag workflow for chDB packages.",
) -> dict:
    return {
        "session_id": "session-1",
        "agent": "codex",
        "source_hash": source_hash,
        "project_id": "chdb-io/chdb",
        "cwd": "/work/chdb",
        "candidates": [
            {
                "content": content,
                "kind": "decision",
                "tags": ["release", "workflow"],
                "suggested_scope": "project",
                "reason": "Stable release policy",
                "evidence": "The team confirmed releases are tag driven.",
                "confidence": 0.95,
            }
        ],
    }


def _ingest(backend, *, run_id: str = "run-1", **session_kwargs):
    return pickup.ingest_run(
        run_id=run_id,
        summarizer="codex",
        prompt_version="pickup-v1",
        since_at="2026-08-03T00:00:00+00:00",
        sessions=[_session(**session_kwargs)],
        backend=backend,
    )


def test_ingest_is_idempotent_and_candidates_are_not_recalled(backend):
    first = _ingest(backend)
    second = _ingest(backend)

    assert first["candidate_count"] == 1
    assert second["skipped"] == "duplicate"
    candidates = pickup.list_candidates(backend=backend)
    assert candidates["total"] == 1
    assert candidates["items"][0]["status"] == "pending"
    assert recall("release tag workflow", project_id="chdb-io/chdb", backend=backend) == []


def test_explicit_personal_accept_promotes_one_candidate(backend):
    _ingest(backend)
    candidate = pickup.list_candidates(backend=backend)["items"][0]

    result = pickup.accept(
        candidate["id"],
        version=candidate["version"],
        scope="personal",
        backend=backend,
    )

    assert result["accepted"] is True
    assert result["candidate"]["status"] == "accepted_personal"
    assert result["candidate"]["memory_ref"].startswith("personal:")
    hits = recall("release tag workflow", project_id="chdb-io/chdb", backend=backend)
    assert hits


def test_stale_version_cannot_decide_twice(backend):
    _ingest(backend)
    candidate = pickup.list_candidates(backend=backend)["items"][0]
    pickup.dismiss(
        candidate["id"],
        version=candidate["version"],
        reason="temporary",
        backend=backend,
    )

    with pytest.raises(pickup.CandidateVersionConflict):
        pickup.accept(
            candidate["id"],
            version=candidate["version"],
            scope="personal",
            backend=backend,
        )


def test_changed_session_supersedes_only_pending_candidates(backend):
    _ingest(backend)
    _ingest(
        backend,
        run_id="run-2",
        source_hash="hash-v2",
        content="Use a signed release tag workflow for chDB packages.",
    )

    pending = pickup.list_candidates(status="pending", backend=backend)
    superseded = pickup.list_candidates(status="superseded", backend=backend)
    assert pending["total"] == 1
    assert superseded["total"] == 1
    assert superseded["items"][0]["decision_reason"] == "source session changed"


def test_snoozed_candidate_reopens_on_next_daily_run(backend):
    _ingest(backend)
    candidate = pickup.list_candidates(backend=backend)["items"][0]
    pickup.snooze(candidate["id"], version=candidate["version"], backend=backend)
    assert pickup.list_candidates(status="pending", backend=backend)["total"] == 0

    pickup.ingest_run(
        run_id="run-next-day",
        summarizer="codex",
        prompt_version="pickup-v1",
        since_at="2026-08-04T00:00:00+00:00",
        sessions=[],
        backend=backend,
    )
    reopened = pickup.list_candidates(status="pending", backend=backend)
    assert reopened["total"] == 1
    assert reopened["items"][0]["version"] == candidate["version"] + 2


def test_project_cas_conflict_keeps_candidate_pending(monkeypatch, backend):
    from clickmem import scopes

    _ingest(backend)
    candidate = pickup.list_candidates(backend=backend)["items"][0]
    monkeypatch.setattr(
        scopes,
        "remember_project",
        lambda *args, **kwargs: {"status": "cas_conflict", "id": "", "message": "retry"},
    )

    result = pickup.accept(
        candidate["id"],
        version=candidate["version"],
        scope="project",
        backend=backend,
    )
    assert result["accepted"] is False
    assert pickup.get(candidate["id"], backend=backend).status == "pending"


def test_deterministic_candidate_id_uses_source_and_content(backend):
    _ingest(backend)
    candidate = pickup.list_candidates(backend=backend)["items"][0]
    expected = hashlib.sha256(
        "\0".join(
            (
                "codex",
                "session-1",
                "hash-v1",
                "Use the release tag workflow for chDB packages.",
            )
        ).encode("utf-8")
    ).hexdigest()[:32]
    assert candidate["id"] == expected
