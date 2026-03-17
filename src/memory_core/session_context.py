"""Session Context — in-memory store for tracking evolving conversation topics.

Maintains per-session topic embeddings via exponential moving average (EMA)
so that retrieval can filter memories by current task relevance.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SessionContext:
    """Tracks the evolving topic of a single session."""

    session_id: str
    topic_embedding: list[float] | None = None  # EMA of query vectors
    recent_queries: list[str] = field(default_factory=list)  # last N for debugging
    call_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _l2_normalize(vec: list[float]) -> list[float]:
    """L2-normalize a vector in-place."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-10:
        return vec
    return [x / norm for x in vec]


class SessionStore:
    """Thread-safe in-memory store for session topic tracking."""

    def __init__(self, alpha: float = 0.3, max_recent: int = 5, ttl_seconds: int = 7200):
        self._sessions: dict[str, SessionContext] = {}
        self._lock = threading.Lock()
        self._alpha = alpha  # EMA weight for new query
        self._max_recent = max_recent
        self._ttl_seconds = ttl_seconds  # 2h default

    def update(self, session_id: str, query_embedding: list[float], query_text: str = "") -> SessionContext:
        """Update session topic with a new query vector via EMA."""
        with self._lock:
            ctx = self._sessions.get(session_id)
            if ctx is None:
                ctx = SessionContext(
                    session_id=session_id,
                    topic_embedding=list(query_embedding),  # first call: use as-is
                    call_count=1,
                )
                if query_text:
                    ctx.recent_queries.append(query_text)
                self._sessions[session_id] = ctx
                return ctx

            # EMA update: topic = alpha * query_vec + (1-alpha) * topic
            if ctx.topic_embedding is not None and len(ctx.topic_embedding) == len(query_embedding):
                alpha = self._alpha
                ctx.topic_embedding = _l2_normalize([
                    alpha * q + (1 - alpha) * t
                    for q, t in zip(query_embedding, ctx.topic_embedding)
                ])
            else:
                ctx.topic_embedding = list(query_embedding)

            ctx.call_count += 1
            ctx.last_active = datetime.now(timezone.utc)
            if query_text:
                ctx.recent_queries.append(query_text)
                if len(ctx.recent_queries) > self._max_recent:
                    ctx.recent_queries = ctx.recent_queries[-self._max_recent:]

            return ctx

    def get(self, session_id: str) -> SessionContext | None:
        """Get session context without modifying it."""
        with self._lock:
            return self._sessions.get(session_id)

    def get_topic_embedding(self, session_id: str) -> list[float] | None:
        """Get just the topic embedding for a session."""
        with self._lock:
            ctx = self._sessions.get(session_id)
            return ctx.topic_embedding if ctx else None

    def cleanup_expired(self) -> int:
        """Remove sessions older than TTL. Returns count removed."""
        now = datetime.now(timezone.utc)
        with self._lock:
            expired = [
                sid for sid, ctx in self._sessions.items()
                if (now - ctx.last_active).total_seconds() > self._ttl_seconds
            ]
            for sid in expired:
                del self._sessions[sid]
            return len(expired)

    def remove(self, session_id: str) -> None:
        """Remove a specific session."""
        with self._lock:
            self._sessions.pop(session_id, None)


_singleton_store: SessionStore | None = None
_singleton_lock = threading.Lock()


def get_session_store() -> SessionStore:
    """Module-level singleton for the session store."""
    global _singleton_store
    if _singleton_store is None:
        with _singleton_lock:
            if _singleton_store is None:
                _singleton_store = SessionStore()
    return _singleton_store
