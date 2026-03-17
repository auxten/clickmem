"""Factory helpers for creating Memory / CEO-entity instances in tests.

Provides MemoryFactory class, CEO entity factories, and convenience functions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from memory_core.models import Decision, Episode, Memory, Principle, Project


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


# ---------------------------------------------------------------------------
# CEO entity factories
# ---------------------------------------------------------------------------

class ProjectFactory:
    _counter = 0

    @classmethod
    def reset(cls) -> None:
        cls._counter = 0

    @classmethod
    def build(cls, **overrides) -> Project:
        cls._counter += 1
        now = datetime.now(timezone.utc)
        defaults = {
            "id": str(uuid.uuid4()),
            "name": f"Project {cls._counter}",
            "description": f"Test project #{cls._counter}",
            "status": "building",
            "vision": "Build something great",
            "target_users": "developers",
            "north_star_metric": "weekly active users",
            "tech_stack": ["python", "chdb"],
            "repo_url": f"/home/user/project{cls._counter}",
            "related_files": [],
            "metadata": "",
            "embedding": None,
            "created_at": now,
            "updated_at": now,
        }
        defaults.update(overrides)
        return Project(**defaults)


class DecisionFactory:
    _counter = 0

    @classmethod
    def reset(cls) -> None:
        cls._counter = 0

    @classmethod
    def build(cls, **overrides) -> Decision:
        cls._counter += 1
        now = datetime.now(timezone.utc)
        defaults = {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "title": f"Decision #{cls._counter}",
            "context": f"Context for decision {cls._counter}",
            "choice": f"Choice {cls._counter}",
            "reasoning": f"Because of reason {cls._counter}",
            "alternatives": "",
            "outcome": "",
            "outcome_status": "pending",
            "domain": "tech",
            "tags": [f"tag{cls._counter}"],
            "source_episodes": [],
            "activation_scope": [],
            "embedding": None,
            "scope_embedding": None,
            "created_at": now,
            "updated_at": now,
        }
        defaults.update(overrides)
        return Decision(**defaults)


class PrincipleFactory:
    _counter = 0

    @classmethod
    def reset(cls) -> None:
        cls._counter = 0

    @classmethod
    def build(cls, **overrides) -> Principle:
        cls._counter += 1
        now = datetime.now(timezone.utc)
        defaults = {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "content": f"Principle #{cls._counter}",
            "domain": "tech",
            "confidence": 0.5,
            "evidence_count": 1,
            "source_decisions": [],
            "activation_scope": [],
            "embedding": None,
            "scope_embedding": None,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        defaults.update(overrides)
        return Principle(**defaults)


class EpisodeFactory:
    _counter = 0

    @classmethod
    def reset(cls) -> None:
        cls._counter = 0

    @classmethod
    def build(cls, **overrides) -> Episode:
        cls._counter += 1
        now = datetime.now(timezone.utc)
        defaults = {
            "id": str(uuid.uuid4()),
            "project_id": "",
            "session_id": f"session-{cls._counter}",
            "agent_source": "claude_code",
            "content": f"Episode #{cls._counter}: user worked on feature",
            "user_intent": f"Implement feature {cls._counter}",
            "key_outcomes": [f"outcome-{cls._counter}"],
            "domain": "tech",
            "tags": [f"tag{cls._counter}"],
            "entities": [],
            "raw_id": "",
            "embedding": None,
            "created_at": now,
        }
        defaults.update(overrides)
        return Episode(**defaults)


def seed_projects(count: int = 2) -> list[Project]:
    """Create seed projects."""
    projects = [
        ProjectFactory.build(
            name="ClickMem",
            description="AI coding assistant memory enhancement",
            status="building",
            tech_stack=["python", "chdb", "fastapi"],
            repo_url="/home/user/clickmem",
        ),
        ProjectFactory.build(
            name="OpenClaw",
            description="Open-source AI agent framework",
            status="ideation",
            tech_stack=["python", "asyncio"],
            repo_url="/home/user/openclaw",
        ),
    ]
    return projects[:count]


def seed_decisions(count: int = 3, project_id: str = "") -> list[Decision]:
    """Create seed decisions."""
    items = [
        DecisionFactory.build(
            project_id=project_id,
            title="Choose chDB over sqlite-vec",
            context="Needed embedded vector DB",
            choice="chDB",
            reasoning="Better SQL support, ClickHouse ecosystem",
            domain="tech",
        ),
        DecisionFactory.build(
            project_id=project_id,
            title="Use Qwen3 embeddings",
            context="Need local embedding model",
            choice="Qwen/Qwen3-Embedding-0.6B",
            reasoning="Small, fast, good quality",
            domain="tech",
        ),
        DecisionFactory.build(
            project_id=project_id,
            title="Local-first architecture",
            context="Privacy and latency concerns",
            choice="All processing on-device",
            reasoning="CEO owns their data",
            domain="product",
        ),
    ]
    return items[:count]


def seed_principles(count: int = 3, project_id: str = "") -> list[Principle]:
    """Create seed principles."""
    items = [
        PrincipleFactory.build(
            project_id=project_id,
            content="Local-first: avoid cloud dependencies for core features",
            domain="product",
            confidence=0.8,
            evidence_count=5,
        ),
        PrincipleFactory.build(
            project_id=project_id,
            content="Quality of extraction matters more than quantity",
            domain="tech",
            confidence=0.7,
            evidence_count=3,
        ),
        PrincipleFactory.build(
            project_id=project_id,
            content="Keep the API surface small and composable",
            domain="tech",
            confidence=0.6,
            evidence_count=2,
        ),
    ]
    return items[:count]


def seed_episodes(count: int = 5, project_id: str = "") -> list[Episode]:
    """Create seed episodes."""
    now = datetime.now(timezone.utc)
    items = [
        EpisodeFactory.build(
            project_id=project_id,
            content="Implemented chDB storage layer with ReplacingMergeTree",
            user_intent="Set up database layer",
            key_outcomes=["MemoryDB class created", "CRUD operations working"],
            created_at=now - timedelta(days=0),
        ),
        EpisodeFactory.build(
            project_id=project_id,
            content="Added embedding support with Qwen3",
            user_intent="Enable semantic search",
            key_outcomes=["EmbeddingEngine working", "Vector search functional"],
            created_at=now - timedelta(days=1),
        ),
        EpisodeFactory.build(
            project_id=project_id,
            content="Built extraction pipeline with LLM",
            user_intent="Auto-extract memories from conversations",
            key_outcomes=["Extractor class done", "Multi-type extraction"],
            created_at=now - timedelta(days=2),
        ),
        EpisodeFactory.build(
            project_id=project_id,
            content="Debugged hook integration with Claude Code",
            user_intent="Fix session lifecycle",
            key_outcomes=["Hooks working end-to-end"],
            created_at=now - timedelta(days=3),
        ),
        EpisodeFactory.build(
            project_id=project_id,
            content="Designed CEO Brain architecture",
            user_intent="Plan system redesign",
            key_outcomes=["PLAN-CEO-BRAIN.md written"],
            created_at=now - timedelta(days=4),
        ),
    ]
    return items[:count]
