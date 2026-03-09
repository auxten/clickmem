"""Tests for the transport abstraction layer — LocalTransport and RemoteTransport."""

from __future__ import annotations

import json

import pytest

from memory_core.models import RetrievalConfig
from memory_core.transport import LocalTransport, RemoteTransport, get_transport


class TestLocalTransport:
    """Test LocalTransport (direct in-process memory operations)."""

    @pytest.fixture
    def transport(self):
        from tests.helpers.mock_embedding import MockEmbeddingEngine
        t = LocalTransport(db_path=":memory:")
        t._get_db()._truncate()
        mock_emb = MockEmbeddingEngine(dimension=256)
        mock_emb.load()
        t._emb = mock_emb
        return t

    def test_health(self, transport):
        result = transport.health()
        assert result["status"] == "ok"
        assert "total_memories" in result

    def test_remember_and_recall(self, transport):
        result = transport.remember("Python is the preferred language", layer="semantic",
                                    category="preference", tags=["python"], no_upsert=True)
        assert result["status"] == "stored"
        assert result["layer"] == "semantic"
        assert "id" in result

        results = transport.recall("Python language")
        assert len(results) > 0
        assert any("Python" in r["content"] for r in results)

    def test_remember_working(self, transport):
        result = transport.remember("Debugging auth flow", layer="working")
        assert result["status"] == "stored"
        assert result["layer"] == "working"

    def test_remember_episodic(self, transport):
        result = transport.remember("Decided to use FastAPI", layer="episodic",
                                    category="decision")
        assert result["status"] == "stored"
        assert result["layer"] == "episodic"

    def test_recall_with_config(self, transport):
        transport.remember("Test item A", layer="semantic", no_upsert=True)
        transport.remember("Test item B", layer="episodic", no_upsert=True)

        cfg = RetrievalConfig(top_k=5, layer="semantic")
        results = transport.recall("test", cfg=cfg)
        assert all(r["layer"] == "semantic" for r in results)

    def test_recall_min_score_filters(self, transport):
        transport.remember("Unique foobar content", layer="semantic", no_upsert=True)
        results = transport.recall("foobar", min_score=0.99)
        # Very high threshold should filter most results
        assert isinstance(results, list)

    def test_forget_by_id(self, transport):
        result = transport.remember("To be forgotten", layer="semantic", no_upsert=True)
        mid = result["id"]

        forget_result = transport.forget(mid)
        assert forget_result["status"] == "deleted"
        assert forget_result["id"] == mid

    def test_forget_not_found(self, transport):
        result = transport.forget("nonexistent-id-xyz")
        assert "error" in result

    def test_review_semantic(self, transport):
        transport.remember("Fact one", layer="semantic", no_upsert=True)
        transport.remember("Fact two", layer="semantic", no_upsert=True)

        memories = transport.review(layer="semantic")
        assert isinstance(memories, list)
        assert len(memories) >= 2

    def test_review_working(self, transport):
        transport.remember("Working context", layer="working")
        result = transport.review(layer="working")
        assert result is not None
        assert "Working context" in str(result)

    def test_review_working_empty(self, transport):
        result = transport.review(layer="working")
        assert result is None

    def test_status(self, transport):
        transport.remember("Semantic fact", layer="semantic", no_upsert=True)
        transport.remember("Episodic event", layer="episodic")

        data = transport.status()
        assert "counts" in data
        assert "total" in data
        assert data["total"] >= 2

    def test_maintain_dry_run(self, transport):
        result = transport.maintain(dry_run=True)
        assert result["dry_run"] is True
        assert "would_clean_stale" in result

    def test_sql(self, transport):
        transport.remember("SQL test", layer="semantic", no_upsert=True)
        results = transport.sql("SELECT count() as cnt FROM memories FINAL WHERE is_active = 1")
        assert len(results) > 0
        assert int(results[0]["cnt"]) >= 1

    def test_extract(self, transport, monkeypatch):
        monkeypatch.setattr("memory_core.llm.get_llm_complete", lambda: None)
        ids = transport.extract("user: I prefer pytest over unittest")
        assert isinstance(ids, list)
        assert len(ids) >= 1


class TestGetTransport:
    """Test the get_transport factory function.

    ``get_transport()`` is for **client** use only (CLI, plugins).
    It never opens chDB directly — it requires a running API server.
    """

    def test_no_server_raises(self):
        """Without a running server, get_transport() raises RuntimeError."""
        with pytest.raises(RuntimeError, match="No ClickMem API server"):
            get_transport()

    def test_remote_url_returns_remote(self):
        t = get_transport(remote="http://localhost:9527")
        assert isinstance(t, RemoteTransport)

    def test_env_var_remote(self, monkeypatch):
        monkeypatch.setenv("CLICKMEM_REMOTE", "http://10.0.0.1:9527")
        t = get_transport()
        assert isinstance(t, RemoteTransport)

    def test_env_var_overridden_by_arg(self, monkeypatch):
        monkeypatch.setenv("CLICKMEM_REMOTE", "http://old:9527")
        t = get_transport(remote="http://new:9527")
        assert isinstance(t, RemoteTransport)


class TestRemoteTransport:
    """Test RemoteTransport HTTP client (with mocked httpx)."""

    @pytest.fixture
    def mock_httpx(self, monkeypatch):
        """Provide a mock httpx.Client that records calls."""
        calls = []

        class MockResponse:
            def __init__(self, data, status=200):
                self._data = data
                self.status_code = status

            def json(self):
                return self._data

            def raise_for_status(self):
                if self.status_code >= 400:
                    import httpx
                    raise httpx.HTTPStatusError(
                        "error", request=None, response=self,
                    )

        class MockClient:
            def __init__(self, **kwargs):
                self.base_url = kwargs.get("base_url", "")
                self.headers = kwargs.get("headers", {})
                self.timeout = kwargs.get("timeout", 30)

            def post(self, path, **kwargs):
                calls.append(("POST", path, kwargs))
                if "/recall" in path:
                    return MockResponse({"memories": [
                        {"id": "abc", "layer": "semantic", "category": "knowledge",
                         "content": "test memory", "final_score": 0.85, "tags": []}
                    ]})
                if "/remember" in path:
                    return MockResponse({"id": "new-id", "status": "stored", "layer": "semantic"})
                if "/extract" in path:
                    return MockResponse({"ids": ["id1", "id2"]})
                if "/maintain" in path:
                    return MockResponse({"dry_run": True, "would_clean_stale": 0})
                if "/sql" in path:
                    return MockResponse({"results": [{"cnt": "5"}]})
                return MockResponse({})

            def get(self, path, **kwargs):
                calls.append(("GET", path, kwargs))
                if "/health" in path:
                    return MockResponse({"status": "ok", "total_memories": 42})
                if "/status" in path:
                    return MockResponse({"counts": {"working": 1, "episodic": 10, "semantic": 30}, "total": 41})
                if "/review" in path:
                    params = kwargs.get("params", {})
                    if params.get("layer") == "working":
                        return MockResponse({"layer": "working", "content": "current focus"})
                    return MockResponse({"layer": "semantic", "memories": [
                        {"id": "x", "content": "fact", "category": "knowledge"}
                    ]})
                return MockResponse({})

            def delete(self, path, **kwargs):
                calls.append(("DELETE", path, kwargs))
                return MockResponse({"id": "del-id", "content": "forgotten", "status": "deleted"})

        import memory_core.transport as transport_mod
        monkeypatch.setattr("httpx.Client", MockClient)
        return calls

    def test_recall(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        results = t.recall("test query")
        assert len(results) == 1
        assert results[0]["content"] == "test memory"
        assert mock_httpx[0][0] == "POST"
        assert "/recall" in mock_httpx[0][1]

    def test_recall_with_config(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        cfg = RetrievalConfig(top_k=5, layer="semantic")
        results = t.recall("test", cfg=cfg)
        body = mock_httpx[0][2]["json"]
        assert body["top_k"] == 5
        assert body["layer"] == "semantic"

    def test_remember(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.remember("new fact", layer="semantic", tags=["tag1"])
        assert result["status"] == "stored"

    def test_extract(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        ids = t.extract("conversation text")
        assert ids == ["id1", "id2"]

    def test_forget(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.forget("some-id")
        assert result["status"] == "deleted"
        assert mock_httpx[0][0] == "DELETE"

    def test_health(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.health()
        assert result["status"] == "ok"
        assert result["total_memories"] == 42

    def test_status(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.status()
        assert result["total"] == 41

    def test_review_semantic(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.review(layer="semantic")
        assert isinstance(result, list)

    def test_review_working(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.review(layer="working")
        assert result == "current focus"

    def test_maintain(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.maintain(dry_run=True)
        assert result["dry_run"] is True

    def test_sql(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527")
        result = t.sql("SELECT 1")
        assert result == [{"cnt": "5"}]

    def test_api_key_in_header(self, mock_httpx):
        t = RemoteTransport("http://localhost:9527", api_key="secret")
        assert t._client.headers.get("Authorization") == "Bearer secret"
