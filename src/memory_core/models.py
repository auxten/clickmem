"""Memory data models.

Defines the core data structures used throughout the memory system.
Implementation team should add validation logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Memory:
    """A single memory entry stored in one of the three layers."""

    content: str
    layer: str = "semantic"  # "working" | "episodic" | "semantic"
    category: str = "knowledge"  # decision | preference | event | person | project | knowledge | todo | insight
    tags: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None
    session_id: Optional[str] = None
    source: str = "agent"  # agent | cli | user_edit | compaction_flush | maintenance
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_active: bool = True
    access_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    accessed_at: Optional[datetime] = None


@dataclass
class RetrievalConfig:
    """Configuration for hybrid search retrieval."""

    top_k: int = 15
    w_vector: float = 0.5
    w_keyword: float = 0.5
    decay_days: float = 60.0
    mmr_lambda: float = 0.7
    layer: Optional[str] = None  # filter by layer; None = all applicable
    category: Optional[str] = None  # filter by category; None = all
