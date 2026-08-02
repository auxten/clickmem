"""Personal/project routing and project-worker CAS behaviour."""

from __future__ import annotations

import pytest


def _project_hit() -> dict:
    return {
        "id": "project-1",
        "content": "shared project decision",
        "kind": "decision",
        "project_id": "chdb-io/chdb",
        "privacy": "public",
        "status": "active",
        "pinned": False,
        "cosine_sim": 0.99,
        "score": 0.99,
        "project_boost": 1.0,
        "tag_boost": 1.0,
        "tag_match_count": 1,
        "source": "team",
        "tags": ["architecture"],
        "updated_at": "2026-08-03T00:00:00Z",
        "scope": "project",
    }


def test_personal_remember_is_default_and_annotated(backend):
    from clickmem.transport import LocalTransport

    result = LocalTransport().remember(
        "personal preference",
        project_id="chdb-io/chdb",
        tags=["workflow"],
    )
    assert result["status"] == "added"
    assert result["scope"] == "personal"


def test_project_remember_routes_to_cold_worker(monkeypatch, backend):
    from clickmem import scopes
    from clickmem.transport import LocalTransport

    seen = {}

    def fake_remember(content, **kwargs):
        seen.update({"content": content, **kwargs})
        return {"status": "added", "id": "team-1", "scope": "project"}

    monkeypatch.setattr(scopes, "remember_project", fake_remember)
    result = LocalTransport().remember(
        "shared rule",
        project_id="chdb-io/chdb",
        tags=["architecture"],
        scope="project",
    )
    assert result == {"status": "added", "id": "team-1", "scope": "project"}
    assert seen["project_id"] == "chdb-io/chdb"


def test_recall_all_merges_personal_and_project(monkeypatch, backend):
    from clickmem import memories, scopes

    memories.add(
        "personal project note",
        project_id="chdb-io/chdb",
        privacy="public",
        tags=["architecture"],
    )
    monkeypatch.setattr(scopes, "project_scope_configured", lambda: True)
    monkeypatch.setattr(
        scopes,
        "run_project_operation",
        lambda op, payload: {"hits": [_project_hit()], "scope": "project"},
    )

    result = scopes.recall_scoped(
        "project decision",
        scope="all",
        project_id="chdb-io/chdb",
        tags=["architecture"],
        backend=backend,
    )
    assert {hit["scope"] for hit in result["hits"]} == {"personal", "project"}
    assert result["scope"] == "all"


def test_explicit_project_recall_requires_configuration(backend):
    from clickmem.scopes import ProjectScopeUnavailable, recall_scoped

    with pytest.raises(ProjectScopeUnavailable):
        recall_scoped(
            "anything",
            scope="project",
            project_id="chdb-io/chdb",
            backend=backend,
        )


async def test_project_cas_conflict_is_http_409(client, monkeypatch):
    from clickmem import scopes

    monkeypatch.setattr(
        scopes,
        "remember_project",
        lambda *args, **kwargs: {
            "status": "cas_conflict",
            "scope": "project",
            "message": "retry against latest revision",
        },
    )
    response = await client.post(
        "/v1/memories",
        json={
            "content": "shared decision",
            "project_id": "chdb-io/chdb",
            "tags": ["architecture"],
            "scope": "project",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "cas_conflict"


def test_project_worker_retries_a_short_cas_lease(monkeypatch):
    from clickmem import config, project_worker

    monkeypatch.setenv("CLICKMEM_PROJECT_DURABLE_URL", "s3://bucket/projects")
    monkeypatch.setenv("CLICKMEM_PROJECT_DURABLE_ID", "chdb-io")
    monkeypatch.setenv("CLICKMEM_PROJECT_WRITE_TIMEOUT_SECONDS", "0.2")
    config._cached = None

    LeaseError = type("LeaseError", (Exception,), {})

    class FakeBackend:
        pass

    attempts = {"count": 0}

    def fake_backend(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise LeaseError("writer won the first CAS")
        return FakeBackend()

    monkeypatch.setattr(project_worker, "DurableBackend", fake_backend)
    opened = project_worker._open_backend(read_only=False)
    assert isinstance(opened, FakeBackend)
    assert attempts["count"] == 2


def test_project_worker_commits_or_discards_one_cold_batch(monkeypatch):
    from clickmem import memories, project_worker

    class FakeBackend:
        def __init__(self):
            self.calls = []

        def commit(self):
            self.calls.append("commit")

        def discard_pending(self):
            self.calls.append("discard")

        def close(self):
            self.calls.append("close")

    committed = FakeBackend()
    monkeypatch.setattr(project_worker, "_open_backend", lambda **kwargs: committed)
    monkeypatch.setattr(memories, "bulk", lambda **kwargs: {"status": "ok"})
    result = project_worker.handle(
        {"op": "bulk", "payload": {"ids": [], "op": "pin"}}
    )
    assert result["status"] == "ok"
    assert committed.calls == ["commit", "close"]

    discarded = FakeBackend()
    monkeypatch.setattr(project_worker, "_open_backend", lambda **kwargs: discarded)
    with pytest.raises(ValueError, match="unknown project operation"):
        project_worker.handle({"op": "not-real", "payload": {}})
    assert discarded.calls == ["discard", "close"]


def test_raw_transcript_never_routes_to_project_worker(monkeypatch, backend):
    from clickmem import raw, scopes

    def unexpected(*args, **kwargs):
        raise AssertionError("raw evidence must stay in the personal actor")

    monkeypatch.setattr(scopes, "run_project_operation", unexpected)
    result = raw.append(
        "raw evidence",
        session_id="session-1",
        project_id="chdb-io/chdb",
        backend=backend,
    )
    assert result["ok"] is True
