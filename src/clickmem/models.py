"""Domain dataclasses: ``Memory``, ``Project``, ``Blacklist``, ``MemoryHistoryEntry``.

These are plain dataclasses with JSON (de)serialisation helpers. The HTTP /
MCP / CLI / portable layers all reuse this serialisation so the wire format
stays consistent.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, List


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime:
    if value is None or value == "":
        return _utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    s = str(value).replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dt_iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).isoformat()


VALID_KINDS = {"principle", "decision", "fact", "doc", "free"}
VALID_PRIVACY = {"public", "private", "confidential"}
VALID_STATUS = {"active", "contracted", "conflicted"}
VALID_OPS = {"expand", "revise", "contract", "pin", "unpin", "resolve"}


@dataclass
class Memory:
    """A single explicit memory — the first-class entity of ClickMem."""

    id: str = ""
    content: str = ""
    kind: str = "free"
    source: str = "agent_remember"
    source_ref: str = ""
    project_id: str = ""
    privacy: str = "private"
    tags: List[str] = field(default_factory=list)
    embedding: List[float] = field(default_factory=list)

    status: str = "active"
    pinned: bool = False
    contract_reason: str = ""
    revises_id: str = ""
    conflict_with: List[str] = field(default_factory=list)

    content_hash: str = ""
    recall_hits: int = 0

    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def to_dict(self, include_embedding: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_embedding:
            data.pop("embedding", None)
        data["pinned"] = bool(self.pinned)
        data["created_at"] = _dt_iso(self.created_at)
        data["updated_at"] = _dt_iso(self.updated_at)
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Memory":
        return cls(
            id=str(row.get("id", "")),
            content=str(row.get("content", "")),
            kind=str(row.get("kind", "free")) or "free",
            source=str(row.get("source", "agent_remember")) or "agent_remember",
            source_ref=str(row.get("source_ref", "")),
            project_id=str(row.get("project_id", "")),
            privacy=str(row.get("privacy", "private")) or "private",
            tags=list(row.get("tags") or []),
            embedding=[float(x) for x in (row.get("embedding") or [])],
            status=str(row.get("status", "active")) or "active",
            pinned=bool(row.get("pinned", 0)),
            contract_reason=str(row.get("contract_reason", "")),
            revises_id=str(row.get("revises_id", "")),
            conflict_with=list(row.get("conflict_with") or []),
            content_hash=str(row.get("content_hash", "")),
            recall_hits=int(row.get("recall_hits", 0) or 0),
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )


@dataclass
class Project:
    id: str = ""
    name: str = ""
    repo_url: str = ""
    kind: str = "work"
    allowed_cross_refs: List[str] = field(default_factory=list)
    embedding: List[float] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def to_dict(self, include_embedding: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_embedding:
            data.pop("embedding", None)
        data["created_at"] = _dt_iso(self.created_at)
        data["updated_at"] = _dt_iso(self.updated_at)
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Project":
        return cls(
            id=str(row.get("id", "")),
            name=str(row.get("name", "")),
            repo_url=str(row.get("repo_url", "")),
            kind=str(row.get("kind", "work")) or "work",
            allowed_cross_refs=list(row.get("allowed_cross_refs") or []),
            embedding=[float(x) for x in (row.get("embedding") or [])],
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )


@dataclass
class Blacklist:
    id: str = ""
    pattern: str = ""
    scope: str = "global"
    reason: str = ""
    hit_count: int = 0
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = _dt_iso(self.created_at)
        data["updated_at"] = _dt_iso(self.updated_at)
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Blacklist":
        return cls(
            id=str(row.get("id", "")),
            pattern=str(row.get("pattern", "")),
            scope=str(row.get("scope", "global")) or "global",
            reason=str(row.get("reason", "")),
            hit_count=int(row.get("hit_count", 0) or 0),
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )


@dataclass
class MemoryHistoryEntry:
    memory_id: str = ""
    version: int = 0
    op: str = "expand"
    content: str = ""
    edited_by: str = ""
    edited_at: datetime = field(default_factory=_utc_now)
    prev_id: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["edited_at"] = _dt_iso(self.edited_at)
        return data

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "MemoryHistoryEntry":
        return cls(
            memory_id=str(row.get("memory_id", "")),
            version=int(row.get("version", 0) or 0),
            op=str(row.get("op", "expand")) or "expand",
            content=str(row.get("content", "")),
            edited_by=str(row.get("edited_by", "")),
            edited_at=_parse_dt(row.get("edited_at")),
            prev_id=str(row.get("prev_id", "")),
            note=str(row.get("note", "")),
        )


def dump_jsonl(items: Iterable[Any]) -> str:
    return "\n".join(json.dumps(_default_dump(x), ensure_ascii=False) for x in items)


def _default_dump(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict(include_embedding=True) if "include_embedding" in obj.to_dict.__code__.co_varnames else obj.to_dict()
    return obj
