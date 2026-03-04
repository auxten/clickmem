"""MemoryDB — persistent memory storage backed by chDB (embedded ClickHouse)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from chdb import session as chdb_session

from memory_core.models import Memory

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS memories (
    id String,
    layer String,
    category String,
    content String,
    tags Array(String),
    entities Array(String),
    embedding Array(Float32),
    session_id String DEFAULT '',
    source String DEFAULT 'agent',
    is_active UInt8 DEFAULT 1,
    access_count UInt32 DEFAULT 0,
    created_at DateTime64(3, 'UTC'),
    updated_at DateTime64(3, 'UTC'),
    accessed_at DateTime64(3, 'UTC')
) ENGINE = MergeTree()
ORDER BY (id)
"""

# chDB limitation: only one persistent path per process.
# We maintain a singleton session for persistent paths, and allow
# multiple in-memory sessions (which don't conflict).
_persistent_session: chdb_session.Session | None = None
_persistent_path: str | None = None


def _get_session(db_path: str) -> chdb_session.Session:
    """Get or create a chDB session.

    chDB's EmbeddedServer can only be initialized once with a persistent path.
    For `:memory:` or None path, we create a fresh session each time.
    For persistent paths, we reuse the same session.
    """
    global _persistent_session, _persistent_path

    if db_path in (":memory:", "", None):
        return chdb_session.Session()

    if _persistent_session is not None:
        if _persistent_path == db_path:
            return _persistent_session
        # Different persistent path requested — chDB limitation
        # Fall back to in-memory to avoid RuntimeError
        return chdb_session.Session()

    _persistent_session = chdb_session.Session(db_path)
    _persistent_path = db_path
    return _persistent_session


class MemoryDB:
    """Persistent memory storage backed by chDB."""

    def __init__(self, db_path: str = ":memory:"):
        self._session = _get_session(db_path)
        self._session.query(_CREATE_TABLE)

    # -- internal helpers --------------------------------------------------

    def _now_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _escape(self, s: str) -> str:
        """Escape a string for ClickHouse SQL."""
        return s.replace("\\", "\\\\").replace("'", "\\'")

    def _array_literal(self, items: list[str]) -> str:
        escaped = [f"'{self._escape(t)}'" for t in items]
        return f"[{','.join(escaped)}]"

    def _float_array_literal(self, items: list[float]) -> str:
        return f"[{','.join(str(x) for x in items)}]"

    def _row_to_memory(self, row: dict) -> Memory:
        """Convert a query result row dict to a Memory object."""
        created_at = _parse_dt(row.get("created_at"))
        updated_at = _parse_dt(row.get("updated_at"))
        accessed_at = _parse_dt(row.get("accessed_at"))

        return Memory(
            id=row["id"],
            layer=row["layer"],
            category=row["category"],
            content=row["content"],
            tags=row.get("tags", []),
            entities=row.get("entities", []),
            embedding=row.get("embedding"),
            session_id=row.get("session_id") or None,
            source=row.get("source", "agent"),
            is_active=bool(row.get("is_active", 1)),
            access_count=int(row.get("access_count", 0)),
            created_at=created_at,
            updated_at=updated_at,
            accessed_at=accessed_at,
        )

    def _query_json(self, sql: str) -> list[dict]:
        """Execute a query and return parsed JSON rows."""
        result = self._session.query(sql, "JSON")
        if result is None:
            return []
        raw = result.bytes()
        if not raw:
            return []
        parsed = json.loads(raw)
        return parsed.get("data", [])

    def _truncate(self) -> None:
        """Drop all data from the memories table (for test isolation)."""
        self._session.query("TRUNCATE TABLE IF EXISTS memories")

    # -- L0 Working --------------------------------------------------------

    def set_working(self, content: str, **kwargs) -> str:
        now = self._now_str()
        # Deactivate all existing working memories
        self._session.query(
            f"ALTER TABLE memories UPDATE is_active = 0, updated_at = '{now}' "
            f"WHERE layer = 'working' AND is_active = 1"
        )
        self._session.query("OPTIMIZE TABLE memories FINAL")

        mid = str(uuid.uuid4())
        esc_content = self._escape(content)
        self._session.query(
            f"INSERT INTO memories (id, layer, category, content, tags, entities, "
            f"embedding, session_id, source, is_active, access_count, "
            f"created_at, updated_at, accessed_at) VALUES "
            f"('{mid}', 'working', 'knowledge', '{esc_content}', [], [], "
            f"[], '', 'agent', 1, 0, '{now}', '{now}', '{now}')"
        )
        return mid

    def get_working(self) -> str | None:
        rows = self._query_json(
            "SELECT content FROM memories "
            "WHERE layer = 'working' AND is_active = 1 "
            "ORDER BY created_at DESC LIMIT 1"
        )
        if not rows:
            return None
        return rows[0]["content"]

    # -- Generic CRUD ------------------------------------------------------

    def insert(self, memory: Memory) -> str:
        now = self._now_str()
        created = memory.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if memory.created_at else now
        updated = memory.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if memory.updated_at else now
        accessed = memory.accessed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if memory.accessed_at else now

        emb_literal = self._float_array_literal(memory.embedding) if memory.embedding else "[]"
        tags_literal = self._array_literal(memory.tags)
        entities_literal = self._array_literal(memory.entities)
        session_id = self._escape(memory.session_id or "")
        source = self._escape(memory.source)
        is_active = 1 if memory.is_active else 0

        sql = (
            f"INSERT INTO memories (id, layer, category, content, tags, entities, "
            f"embedding, session_id, source, is_active, access_count, "
            f"created_at, updated_at, accessed_at) VALUES "
            f"('{self._escape(memory.id)}', '{self._escape(memory.layer)}', "
            f"'{self._escape(memory.category)}', '{self._escape(memory.content)}', "
            f"{tags_literal}, {entities_literal}, {emb_literal}, "
            f"'{session_id}', '{source}', {is_active}, {memory.access_count}, "
            f"'{created}', '{updated}', '{accessed}')"
        )
        self._session.query(sql)
        return memory.id

    def get(self, memory_id: str) -> Memory | None:
        rows = self._query_json(
            f"SELECT * FROM memories WHERE id = '{self._escape(memory_id)}' "
            f"ORDER BY updated_at DESC LIMIT 1"
        )
        if not rows:
            return None
        return self._row_to_memory(rows[0])

    def update_content(self, memory_id: str, new_content: str) -> str:
        old = self.get(memory_id)
        if old is None:
            raise ValueError(f"Memory {memory_id} not found")

        # Deactivate old
        self.deactivate(memory_id)

        # Create new version
        now = datetime.now(timezone.utc)
        new_memory = Memory(
            content=new_content,
            layer=old.layer,
            category=old.category,
            tags=list(old.tags),
            entities=list(old.entities),
            embedding=old.embedding,
            session_id=old.session_id,
            source=old.source,
            is_active=True,
            access_count=old.access_count,
            created_at=old.created_at,
            updated_at=now,
            accessed_at=now,
        )
        self.insert(new_memory)
        return new_memory.id

    def deactivate(self, memory_id: str) -> bool:
        existing = self.get(memory_id)
        if existing is None:
            return False
        now = self._now_str()
        self._session.query(
            f"ALTER TABLE memories UPDATE is_active = 0, updated_at = '{now}' "
            f"WHERE id = '{self._escape(memory_id)}'"
        )
        self._session.query("OPTIMIZE TABLE memories FINAL")
        return True

    def delete(self, memory_id: str) -> bool:
        existing = self.get(memory_id)
        if existing is None:
            return False
        self._session.query(
            f"ALTER TABLE memories DELETE WHERE id = '{self._escape(memory_id)}'"
        )
        self._session.query("OPTIMIZE TABLE memories FINAL")
        return True

    # -- Queries -----------------------------------------------------------

    def list_by_layer(self, layer: str, *, limit: int = 100) -> list[Memory]:
        rows = self._query_json(
            f"SELECT * FROM memories "
            f"WHERE layer = '{self._escape(layer)}' AND is_active = 1 "
            f"ORDER BY created_at DESC LIMIT {limit}"
        )
        return [self._row_to_memory(r) for r in rows]

    def count(self) -> int:
        rows = self._query_json(
            "SELECT count() as cnt FROM memories WHERE is_active = 1"
        )
        return int(rows[0]["cnt"]) if rows else 0

    def count_by_layer(self) -> dict[str, int]:
        rows = self._query_json(
            "SELECT layer, count() as cnt FROM memories "
            "WHERE is_active = 1 GROUP BY layer"
        )
        result = {"working": 0, "episodic": 0, "semantic": 0}
        for r in rows:
            result[r["layer"]] = int(r["cnt"])
        return result

    def stats(self) -> dict:
        rows = self._query_json(
            "SELECT layer, category, count() as cnt FROM memories "
            "WHERE is_active = 1 GROUP BY layer, category ORDER BY layer, category"
        )
        result: dict = {}
        for r in rows:
            layer = r["layer"]
            cat = r["category"]
            if layer not in result:
                result[layer] = {}
            result[layer][cat] = int(r["cnt"])
        return result

    def find_by_tags(self, tags: list[str]) -> list[Memory]:
        tags_lit = self._array_literal(tags)
        rows = self._query_json(
            f"SELECT * FROM memories "
            f"WHERE hasAny(tags, {tags_lit}) AND is_active = 1"
        )
        return [self._row_to_memory(r) for r in rows]

    def find_stale_episodic(self, decay_days: int = 120) -> list[Memory]:
        rows = self._query_json(
            f"SELECT * FROM memories "
            f"WHERE layer = 'episodic' AND is_active = 1 "
            f"AND access_count = 0 "
            f"AND accessed_at < now() - INTERVAL {decay_days} DAY"
        )
        return [self._row_to_memory(r) for r in rows]

    def find_deleted(self, days: int = 7) -> list[Memory]:
        rows = self._query_json(
            f"SELECT * FROM memories "
            f"WHERE is_active = 0 "
            f"AND updated_at < now() - INTERVAL {days} DAY"
        )
        return [self._row_to_memory(r) for r in rows]

    def get_episodic_by_month(self, month: str) -> list[Memory]:
        # month format: "2026-01"
        rows = self._query_json(
            f"SELECT * FROM memories "
            f"WHERE layer = 'episodic' AND is_active = 1 "
            f"AND formatDateTime(created_at, '%Y-%m') = '{self._escape(month)}' "
            f"ORDER BY created_at"
        )
        return [self._row_to_memory(r) for r in rows]

    def get_tag_frequencies(self, layer: str = "episodic", min_count: int = 3) -> dict[str, int]:
        rows = self._query_json(
            f"SELECT tag, count() as cnt FROM ("
            f"  SELECT arrayJoin(tags) as tag FROM memories "
            f"  WHERE layer = '{self._escape(layer)}' AND is_active = 1"
            f") GROUP BY tag HAVING cnt >= {min_count} ORDER BY cnt DESC"
        )
        return {r["tag"]: int(r["cnt"]) for r in rows}

    # -- Raw SQL -----------------------------------------------------------

    def query(self, sql: str) -> list[dict]:
        return self._query_json(sql)


def _parse_dt(val) -> Optional[datetime]:
    """Parse a datetime string from chDB JSON output."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        s = str(val)
        if "." in s:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")
        else:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
