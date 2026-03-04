"""Factory helpers for creating Memory instances in tests.

Provides MemoryFactory class and convenience make_memory() function.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from memory_core.models import Memory


class MemoryFactory:
    """Factory for building Memory objects with sensible defaults."""

    _counter = 0

    @classmethod
    def reset(cls) -> None:
        cls._counter = 0

    @classmethod
    def build(cls, **overrides) -> Memory:
        """Create a Memory with auto-generated defaults for missing fields."""
        cls._counter += 1
        now = datetime.now(timezone.utc)

        defaults = {
            "id": str(uuid.uuid4()),
            "content": f"Test memory content #{cls._counter}",
            "layer": "episodic",
            "category": "event",
            "tags": [f"tag{cls._counter}"],
            "entities": [],
            "embedding": None,
            "session_id": f"session-{cls._counter}",
            "source": "agent",
            "is_active": True,
            "access_count": 0,
            "created_at": now,
            "updated_at": now,
            "accessed_at": now,
        }
        defaults.update(overrides)
        return Memory(**defaults)

    @classmethod
    def build_batch(cls, count: int, **overrides) -> list[Memory]:
        """Create multiple memories."""
        return [cls.build(**overrides) for _ in range(count)]


def make_memory(**overrides) -> Memory:
    """Shortcut for MemoryFactory.build()."""
    return MemoryFactory.build(**overrides)


def seed_working() -> Memory:
    """Create an L0 working memory entry."""
    return make_memory(
        layer="working",
        category="knowledge",
        content="User is debugging HNSW index config, follow up on bf16 quantization",
        tags=["hnsw", "debugging"],
        source="agent",
    )


def seed_episodic(count: int = 5) -> list[Memory]:
    """Create a batch of L1 episodic memories with varied timestamps."""
    now = datetime.now(timezone.utc)
    memories = []
    events = [
        ("Decided on Python core + JS thin-shell arch", "decision", ["python", "architecture"]),
        ("Aligned API design with Alice, adopted gRPC", "event", ["api", "grpc", "alice"]),
        ("Researched chDB HNSW index, confirmed 25.8 GA", "insight", ["chdb", "hnsw"]),
        ("No graph modeling, keep it simple", "decision", ["architecture"]),
        ("Project kickoff, goal: replace sqlite-vec", "event", ["project", "kickoff"]),
    ]
    for i, (content, category, tags) in enumerate(events[:count]):
        memories.append(make_memory(
            layer="episodic",
            content=content,
            category=category,
            tags=tags,
            created_at=now - timedelta(days=i),
            updated_at=now - timedelta(days=i),
            accessed_at=now - timedelta(days=i),
        ))
    return memories


def seed_semantic(count: int = 5) -> list[Memory]:
    """Create a batch of L2 semantic memories."""
    facts = [
        ("Prefers SwiftUI over UIKit", "preference", ["swift", "ui"]),
        ("Keep answers concise, skip basic explanations", "preference", ["style"]),
        ("iOS developer, based in Singapore", "knowledge", ["profile"]),
        ("Alice is the backend lead", "person", ["alice", "team"]),
        ("Memory system project, goal: replace native", "project", ["memory", "project"]),
    ]
    memories = []
    for content, category, tags in facts[:count]:
        memories.append(make_memory(
            layer="semantic",
            content=content,
            category=category,
            tags=tags,
        ))
    return memories


def seed_stale_episodic(count: int = 3, stale_days: int = 130) -> list[Memory]:
    """Create L1 episodic memories that are stale (old + 0 accesses)."""
    now = datetime.now(timezone.utc)
    stale = []
    for i in range(count):
        stale.append(make_memory(
            layer="episodic",
            content=f"Stale event from {stale_days + i} days ago",
            category="event",
            tags=["stale"],
            access_count=0,
            created_at=now - timedelta(days=stale_days + i),
            updated_at=now - timedelta(days=stale_days + i),
            accessed_at=now - timedelta(days=stale_days + i),
        ))
    return stale


def seed_with_repeated_tag(tag: str, count: int = 4) -> list[Memory]:
    """Create episodic memories that all share a specific tag (for promotion testing)."""
    return [
        make_memory(
            layer="episodic",
            content=f"Event mentioning {tag} #{i+1}",
            category="event",
            tags=[tag, f"extra-{i}"],
        )
        for i in range(count)
    ]
