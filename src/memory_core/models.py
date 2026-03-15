"""Memory data models.

Defines the core data structures used throughout the memory system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Legacy model (kept for migration support, do not use in new code)
# ---------------------------------------------------------------------------

@dataclass
class Memory:
    """A single memory entry stored in one of the three layers. DEPRECATED."""

    content: str
    layer: str = "semantic"
    category: str = "knowledge"
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None
    session_id: Optional[str] = None
    source: str = "agent"
    raw_id: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_active: bool = True
    access_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    accessed_at: Optional[datetime] = None


@dataclass
class RetrievalConfig:
    """Configuration for hybrid search retrieval. DEPRECATED."""

    top_k: int = 15
    w_vector: float = 0.5
    w_keyword: float = 0.5
    decay_days: float = 60.0
    mmr_lambda: float = 0.7
    semantic_boost: float = 1.3
    refinement_boost: float = 1.15
    layer: Optional[str] = None
    category: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None


# ---------------------------------------------------------------------------
# CEO Brain entity models
# ---------------------------------------------------------------------------

@dataclass
class Project:
    """A project the CEO is building or maintaining."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    status: str = "building"  # ideation | building | launched | maintaining | sunset
    vision: str = ""
    target_users: str = ""
    north_star_metric: str = ""
    tech_stack: list[str] = field(default_factory=list)
    repo_url: str = ""
    related_files: list[str] = field(default_factory=list)
    metadata: str = ""  # JSON string for extensibility
    embedding: Optional[list[float]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Decision:
    """A decision made in the context of a project (or globally)."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""  # empty = global decision
    title: str = ""
    context: str = ""
    choice: str = ""
    reasoning: str = ""
    alternatives: str = ""
    outcome: str = ""
    outcome_status: str = "pending"  # pending | validated | invalidated | unknown
    domain: str = "tech"  # product | tech | design | marketing | ops
    tags: list[str] = field(default_factory=list)
    source_episodes: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Principle:
    """A reusable principle or preference, derived from experience."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""  # empty = global principle
    content: str = ""
    domain: str = "tech"  # product | tech | design | marketing | ops | management
    confidence: float = 0.5
    evidence_count: int = 0
    source_decisions: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class Episode:
    """A summarised interaction episode linked to a project."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    session_id: str = ""
    agent_source: str = ""  # claude_code | cursor | openclaw | other
    content: str = ""
    user_intent: str = ""
    key_outcomes: list[str] = field(default_factory=list)
    domain: str = "tech"
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    raw_id: str = ""
    embedding: Optional[list[float]] = None
    created_at: Optional[datetime] = None


@dataclass
class CEORetrievalConfig:
    """Configuration for CEO context retrieval."""

    project_id: Optional[str] = None
    top_k: int = 10
    domain: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    include_global: bool = True  # include global (project_id='') items
