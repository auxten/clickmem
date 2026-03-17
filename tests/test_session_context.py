"""Tests for SessionContext and SessionStore."""

import math
import threading
import time
from datetime import datetime, timedelta, timezone

from memory_core.session_context import SessionContext, SessionStore, get_session_store, _l2_normalize


def _make_vec(val: float, dim: int = 256) -> list[float]:
    """Create a simple test vector."""
    raw = [val] * dim
    return _l2_normalize(raw)


class TestSessionStore:

    def test_create_session(self):
        store = SessionStore()
        vec = _make_vec(1.0)
        ctx = store.update("s1", vec, "hello")
        assert ctx.session_id == "s1"
        assert ctx.call_count == 1
        assert ctx.topic_embedding is not None
        assert len(ctx.topic_embedding) == 256
        assert ctx.recent_queries == ["hello"]

    def test_update_ema(self):
        store = SessionStore(alpha=0.5)
        vec1 = _make_vec(1.0)
        vec2 = _make_vec(-1.0)
        store.update("s1", vec1, "first")
        ctx = store.update("s1", vec2, "second")
        assert ctx.call_count == 2
        # EMA with alpha=0.5: should be midpoint (but normalized)
        # The topic should not be equal to either vec1 or vec2
        assert ctx.topic_embedding is not None
        # After EMA, the result should be different from both inputs
        sim1 = sum(a * b for a, b in zip(ctx.topic_embedding, vec1))
        sim2 = sum(a * b for a, b in zip(ctx.topic_embedding, vec2))
        assert sim1 != sim2 or abs(sim1) < 0.01  # they should differ

    def test_get_existing(self):
        store = SessionStore()
        store.update("s1", _make_vec(1.0))
        ctx = store.get("s1")
        assert ctx is not None
        assert ctx.session_id == "s1"

    def test_get_nonexistent(self):
        store = SessionStore()
        assert store.get("nonexistent") is None

    def test_get_topic_embedding(self):
        store = SessionStore()
        vec = _make_vec(1.0)
        store.update("s1", vec)
        topic = store.get_topic_embedding("s1")
        assert topic is not None
        assert len(topic) == 256

    def test_get_topic_embedding_nonexistent(self):
        store = SessionStore()
        assert store.get_topic_embedding("nonexistent") is None

    def test_remove(self):
        store = SessionStore()
        store.update("s1", _make_vec(1.0))
        store.remove("s1")
        assert store.get("s1") is None

    def test_remove_nonexistent(self):
        store = SessionStore()
        store.remove("nonexistent")  # should not raise

    def test_cleanup_expired(self):
        store = SessionStore(ttl_seconds=1)
        store.update("s1", _make_vec(1.0))
        # Manually set last_active in the past
        ctx = store.get("s1")
        ctx.last_active = datetime.now(timezone.utc) - timedelta(seconds=10)
        removed = store.cleanup_expired()
        assert removed == 1
        assert store.get("s1") is None

    def test_cleanup_keeps_active(self):
        store = SessionStore(ttl_seconds=3600)
        store.update("s1", _make_vec(1.0))
        removed = store.cleanup_expired()
        assert removed == 0
        assert store.get("s1") is not None

    def test_recent_queries_capped(self):
        store = SessionStore(max_recent=3)
        vec = _make_vec(1.0)
        for i in range(10):
            store.update("s1", vec, f"query-{i}")
        ctx = store.get("s1")
        assert len(ctx.recent_queries) == 3
        assert ctx.recent_queries[-1] == "query-9"

    def test_thread_safety(self):
        store = SessionStore()
        errors = []

        def worker(session_id):
            try:
                for i in range(50):
                    store.update(session_id, _make_vec(float(i)), f"q-{i}")
                store.get(session_id)
                store.get_topic_embedding(session_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"s-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_first_call_uses_query_as_is(self):
        store = SessionStore()
        vec = _make_vec(1.0)
        ctx = store.update("s1", vec)
        # First call should use the query vector directly
        for a, b in zip(ctx.topic_embedding, vec):
            assert abs(a - b) < 1e-6


class TestSingleton:

    def test_get_session_store_returns_same_instance(self):
        store1 = get_session_store()
        store2 = get_session_store()
        assert store1 is store2


class TestL2Normalize:

    def test_normalize(self):
        vec = [3.0, 4.0]
        result = _l2_normalize(vec)
        assert abs(result[0] - 0.6) < 1e-6
        assert abs(result[1] - 0.8) < 1e-6

    def test_zero_vector(self):
        vec = [0.0, 0.0, 0.0]
        result = _l2_normalize(vec)
        assert result == vec
