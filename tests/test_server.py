"""Tests for the FastAPI REST API server endpoints.

Uses FastAPI TestClient for in-process HTTP testing — no real server needed.
"""

from __future__ import annotations

import json
import os

import pytest

try:
    from fastapi.testclient import TestClient
    from memory_core.server import app, _get_transport, set_debug_mode
    import memory_core.server as server_mod
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Ensure clean server state for each test."""
    server_mod._transport = None
    server_mod._api_key_env = None
    server_mod._debug_mode = False
    os.environ.pop("CLICKMEM_API_KEY", None)
    yield
    server_mod._transport = None
    server_mod._api_key_env = None
    server_mod._debug_mode = False
    os.environ.pop("CLICKMEM_API_KEY", None)


def _make_test_transport():
    """Create a LocalTransport with mock embedding (avoids loading real model)."""
    from memory_core.transport import LocalTransport
    from tests.helpers.mock_embedding import MockEmbeddingEngine
    t = LocalTransport(db_path=":memory:")
    t._get_db()._truncate()
    mock_emb = MockEmbeddingEngine(dimension=256)
    mock_emb.load()
    t._emb = mock_emb
    return t


@pytest.fixture
def client():
    """Provide a FastAPI TestClient backed by in-memory LocalTransport."""
    server_mod._transport = _make_test_transport()
    return TestClient(app)


@pytest.fixture
def authed_client():
    """Provide a client with API key auth enabled."""
    server_mod._transport = _make_test_transport()
    os.environ["CLICKMEM_API_KEY"] = "test-secret-key"
    server_mod._api_key_env = None  # force re-read
    return TestClient(app)


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "total_memories" in data

    def test_health_no_auth_needed(self, authed_client):
        resp = authed_client.get("/v1/health")
        assert resp.status_code == 200


class TestRemember:
    def test_remember_semantic(self, client):
        resp = client.post("/v1/remember", json={
            "content": "User prefers pytest",
            "layer": "semantic",
            "category": "preference",
            "tags": ["python", "testing"],
            "no_upsert": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stored"
        assert "id" in data

    def test_remember_episodic(self, client):
        resp = client.post("/v1/remember", json={
            "content": "Decided on FastAPI for the server",
            "layer": "episodic",
            "category": "decision",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "stored"

    def test_remember_working(self, client):
        resp = client.post("/v1/remember", json={
            "content": "Currently debugging MCP integration",
            "layer": "working",
        })
        assert resp.status_code == 200
        assert resp.json()["layer"] == "working"


class TestRecall:
    def test_recall_empty_db(self, client):
        resp = client.post("/v1/recall", json={"query": "anything"})
        assert resp.status_code == 200
        assert resp.json()["memories"] == []

    def test_recall_finds_stored_memory(self, client):
        client.post("/v1/remember", json={
            "content": "Python is preferred for backend",
            "layer": "semantic", "no_upsert": True,
        })
        resp = client.post("/v1/recall", json={"query": "Python backend"})
        assert resp.status_code == 200
        memories = resp.json()["memories"]
        assert len(memories) > 0
        assert any("Python" in m["content"] for m in memories)

    def test_recall_with_layer_filter(self, client):
        client.post("/v1/remember", json={
            "content": "Semantic fact", "layer": "semantic", "no_upsert": True,
        })
        client.post("/v1/remember", json={
            "content": "Episodic event", "layer": "episodic",
        })
        resp = client.post("/v1/recall", json={
            "query": "fact event", "layer": "semantic",
        })
        assert resp.status_code == 200
        for m in resp.json()["memories"]:
            assert m["layer"] == "semantic"

    def test_recall_min_score(self, client):
        client.post("/v1/remember", json={
            "content": "UniqueXYZ123 content", "layer": "semantic", "no_upsert": True,
        })
        resp = client.post("/v1/recall", json={
            "query": "UniqueXYZ123", "min_score": 0.99,
        })
        assert resp.status_code == 200

    def test_recall_top_k(self, client):
        for i in range(5):
            client.post("/v1/remember", json={
                "content": f"Memory item {i}", "layer": "semantic", "no_upsert": True,
            })
        resp = client.post("/v1/recall", json={"query": "Memory item", "top_k": 2})
        assert resp.status_code == 200
        assert len(resp.json()["memories"]) <= 2


class TestForget:
    def test_forget_by_id(self, client):
        resp = client.post("/v1/remember", json={
            "content": "To be forgotten", "layer": "semantic", "no_upsert": True,
        })
        mid = resp.json()["id"]

        resp = client.delete(f"/v1/forget/{mid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_forget_not_found(self, client):
        resp = client.delete("/v1/forget/nonexistent-id")
        assert resp.status_code == 404


class TestReview:
    def test_review_semantic(self, client):
        client.post("/v1/remember", json={
            "content": "Review test", "layer": "semantic", "no_upsert": True,
        })
        resp = client.get("/v1/review", params={"layer": "semantic"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["layer"] == "semantic"
        assert len(data["memories"]) >= 1

    def test_review_working(self, client):
        client.post("/v1/remember", json={
            "content": "Working context", "layer": "working",
        })
        resp = client.get("/v1/review", params={"layer": "working"})
        assert resp.status_code == 200
        assert resp.json()["content"] is not None

    def test_review_empty(self, client):
        resp = client.get("/v1/review", params={"layer": "semantic"})
        assert resp.status_code == 200
        assert resp.json()["memories"] == []


class TestStatus:
    def test_status_empty(self, client):
        resp = client.get("/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert "counts" in data

    def test_status_with_data(self, client):
        client.post("/v1/remember", json={
            "content": "Fact", "layer": "semantic", "no_upsert": True,
        })
        client.post("/v1/remember", json={
            "content": "Event", "layer": "episodic",
        })
        resp = client.get("/v1/status")
        data = resp.json()
        assert data["total"] >= 2


class TestMaintain:
    def test_maintain_dry_run(self, client):
        resp = client.post("/v1/maintain", json={"dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True

    def test_maintain_full(self, client):
        resp = client.post("/v1/maintain", json={"dry_run": False})
        assert resp.status_code == 200


class TestExtract:
    def test_extract_text_no_llm(self, client, monkeypatch):
        monkeypatch.setattr("memory_core.llm.get_llm_complete", lambda: None)
        resp = client.post("/v1/extract", json={
            "text": "user: I prefer pytest for testing",
            "session_id": "test-session",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "ids" in data
        assert len(data["ids"]) >= 1


class TestSql:
    def test_sql_blocked_without_debug(self, client):
        resp = client.post("/v1/sql", json={"query": "SELECT 1"})
        assert resp.status_code == 403

    def test_sql_works_in_debug_mode(self, client):
        set_debug_mode(True)
        resp = client.post("/v1/sql", json={"query": "SELECT 1 as one"})
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_sql_invalid_query(self, client):
        set_debug_mode(True)
        resp = client.post("/v1/sql", json={"query": "NOT VALID SQL"})
        assert resp.status_code == 400


class TestAuth:
    def test_no_auth_when_no_key_set(self, client):
        resp = client.get("/v1/status")
        assert resp.status_code == 200

    def test_auth_required_when_key_set(self, authed_client):
        resp = authed_client.get("/v1/status")
        assert resp.status_code == 401

    def test_auth_with_correct_key(self, authed_client):
        resp = authed_client.get("/v1/status", headers={
            "Authorization": "Bearer test-secret-key",
        })
        assert resp.status_code == 200

    def test_auth_with_wrong_key(self, authed_client):
        resp = authed_client.get("/v1/status", headers={
            "Authorization": "Bearer wrong-key",
        })
        assert resp.status_code == 401

    def test_auth_remember(self, authed_client):
        resp = authed_client.post("/v1/remember", json={
            "content": "Secret memory", "layer": "semantic", "no_upsert": True,
        }, headers={"Authorization": "Bearer test-secret-key"})
        assert resp.status_code == 200

    def test_auth_recall(self, authed_client):
        resp = authed_client.post("/v1/recall", json={"query": "test"},
                                  headers={"Authorization": "Bearer test-secret-key"})
        assert resp.status_code == 200


class TestRequestValidation:
    def test_recall_missing_query(self, client):
        resp = client.post("/v1/recall", json={})
        assert resp.status_code == 422

    def test_remember_missing_content(self, client):
        resp = client.post("/v1/remember", json={"layer": "semantic"})
        assert resp.status_code == 422

    def test_recall_top_k_out_of_range(self, client):
        resp = client.post("/v1/recall", json={"query": "test", "top_k": 200})
        assert resp.status_code == 422

    def test_recall_min_score_out_of_range(self, client):
        resp = client.post("/v1/recall", json={"query": "test", "min_score": 5.0})
        assert resp.status_code == 422


class TestCombinedApp:
    """Test that the combined REST + MCP SSE app serves both on one port."""

    @pytest.fixture
    def combined_client(self):
        import memory_core.mcp_server as mcp_mod

        t = _make_test_transport()
        server_mod._transport = t
        mcp_mod._transport = None

        combined = server_mod._build_combined_app()
        return TestClient(combined)

    def test_rest_health_on_combined(self, combined_client):
        resp = combined_client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_rest_remember_on_combined(self, combined_client):
        resp = combined_client.post("/v1/remember", json={
            "content": "Combined server test",
            "layer": "semantic",
            "no_upsert": True,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "stored"

    def test_mcp_transport_shared(self, combined_client):
        """MCP and REST share the same transport after _build_combined_app."""
        import memory_core.mcp_server as mcp_mod
        assert mcp_mod._transport is server_mod._transport
