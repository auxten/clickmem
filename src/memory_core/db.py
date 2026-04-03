"""MemoryDB — persistent memory storage backed by chDB (embedded ClickHouse)."""

from __future__ import annotations

import json
import threading
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
    raw_id String DEFAULT '',
    is_active UInt8 DEFAULT 1,
    access_count UInt32 DEFAULT 0,
    created_at DateTime64(3, 'UTC'),
    updated_at DateTime64(3, 'UTC'),
    accessed_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (id)
"""

_CREATE_RAW_TABLE = """
CREATE TABLE IF NOT EXISTS raw_transcripts (
    id          String,
    session_id  String DEFAULT '',
    source      String DEFAULT 'cursor',
    content     String,
    char_count  UInt32,
    is_processed UInt8 DEFAULT 0,
    processed_at DateTime64(3, 'UTC') DEFAULT '1970-01-01',
    created_at  DateTime64(3, 'UTC')
) ENGINE = MergeTree()
ORDER BY (created_at, id)
"""

_session_lock = threading.Lock()
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

    with _session_lock:
        if _persistent_session is not None:
            if _persistent_path == db_path:
                return _persistent_session
            return chdb_session.Session()

        _persistent_session = chdb_session.Session(db_path)
        _persistent_path = db_path
        return _persistent_session


class MemoryDB:
    """Persistent memory storage backed by chDB.

    Uses ReplacingMergeTree(updated_at) so that row-level updates are
    performed as INSERT (append) instead of ALTER TABLE UPDATE mutations.
    All SELECTs use FINAL to see the latest version of each row.
    This eliminates mutation locks and OPTIMIZE TABLE FINAL from hot paths.
    """

    def __init__(self, db_path: str = ":memory:"):
        self._session = _get_session(db_path)
        self._session.query(_CREATE_TABLE)
        self._session.query(_CREATE_RAW_TABLE)

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
            raw_id=row.get("raw_id") or None,
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
        """Drop all data from memories and raw_transcripts tables (for test isolation)."""
        self._session.query("TRUNCATE TABLE IF EXISTS memories")
        self._session.query("TRUNCATE TABLE IF EXISTS raw_transcripts")

    # -- L0 Working (deprecated — agents manage their own session context) --

    def set_working(self, content: str, **kwargs) -> str:
        now_dt = datetime.now(timezone.utc)

        old_rows = self._query_json(
            "SELECT * FROM memories FINAL "
            "WHERE layer = 'working' AND is_active = 1"
        )
        for row in old_rows:
            old = self._row_to_memory(row)
            old.is_active = False
            old.updated_at = now_dt
            self.insert(old)

        m = Memory(
            content=content,
            layer="working",
            category="knowledge",
            is_active=True,
            created_at=now_dt,
            updated_at=now_dt,
            accessed_at=now_dt,
        )
        self.insert(m)
        return m.id

    def get_working(self) -> str | None:
        rows = self._query_json(
            "SELECT content FROM memories FINAL "
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
        raw_id = self._escape(memory.raw_id or "")
        is_active = 1 if memory.is_active else 0

        sql = (
            f"INSERT INTO memories (id, layer, category, content, tags, entities, "
            f"embedding, session_id, source, raw_id, is_active, access_count, "
            f"created_at, updated_at, accessed_at) VALUES "
            f"('{self._escape(memory.id)}', '{self._escape(memory.layer)}', "
            f"'{self._escape(memory.category)}', '{self._escape(memory.content)}', "
            f"{tags_literal}, {entities_literal}, {emb_literal}, "
            f"'{session_id}', '{source}', '{raw_id}', {is_active}, {memory.access_count}, "
            f"'{created}', '{updated}', '{accessed}')"
        )
        self._session.query(sql)
        return memory.id

    def get(self, memory_id: str) -> Memory | None:
        rows = self._query_json(
            f"SELECT * FROM memories FINAL WHERE id = '{self._escape(memory_id)}' "
            f"LIMIT 1"
        )
        if not rows:
            return None
        return self._row_to_memory(rows[0])

    def update_content(self, memory_id: str, new_content: str) -> str:
        old = self.get(memory_id)
        if old is None:
            raise ValueError(f"Memory {memory_id} not found")

        now = datetime.now(timezone.utc)

        old.is_active = False
        old.updated_at = now
        self.insert(old)

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

    def update_embedding(self, memory_id: str, embedding: list[float]) -> None:
        """Update the embedding of a memory by inserting a new version."""
        existing = self.get(memory_id)
        if existing is None:
            return
        existing.embedding = embedding
        existing.updated_at = datetime.now(timezone.utc)
        self.insert(existing)

    def touch(self, memory_id: str) -> None:
        """Bump access_count and accessed_at for a memory."""
        existing = self.get(memory_id)
        if existing is None or not existing.is_active:
            return
        now = datetime.now(timezone.utc)
        existing.access_count += 1
        existing.accessed_at = now
        existing.updated_at = now
        self.insert(existing)

    def deactivate(self, memory_id: str) -> bool:
        existing = self.get(memory_id)
        if existing is None:
            return False
        now = datetime.now(timezone.utc)
        existing.is_active = False
        existing.updated_at = now
        self.insert(existing)
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

    def optimize(self) -> None:
        """Force merge of all parts — call during maintenance, not in hot paths."""
        self._session.query("OPTIMIZE TABLE memories FINAL")

    # -- Queries -----------------------------------------------------------

    @staticmethod
    def _normalize_datetime(dt_str: str) -> str:
        """Normalize ISO-8601 datetime to ClickHouse-compatible format.

        Handles 'T' separator, trailing 'Z', and timezone offsets like +00:00.
        """
        import re
        s = dt_str.replace("T", " ")
        if s.endswith("Z"):
            s = s[:-1]
        s = re.sub(r'[+-]\d{2}:?\d{2}$', '', s)
        return s.strip()

    def _time_conditions(self, since: str | None, until: str | None) -> list[str]:
        """Build SQL WHERE clauses for time filtering.

        Validates input looks like a datetime to prevent injection.
        """
        import re
        _dt_re = re.compile(r"^\d{4}-\d{2}-\d{2}[\sT]?\d{0,2}:?\d{0,2}:?\d{0,2}")
        conds: list[str] = []
        if since and _dt_re.match(since):
            normalized = self._normalize_datetime(since)
            conds.append(f"created_at >= '{self._escape(normalized)}'")
        if until and _dt_re.match(until):
            normalized = self._normalize_datetime(until)
            conds.append(f"created_at <= '{self._escape(normalized)}'")
        return conds

    def list_by_layer(self, layer: str, *, limit: int = 100,
                      since: str | None = None, until: str | None = None) -> list[Memory]:
        conds = [f"layer = '{self._escape(layer)}'", "is_active = 1"]
        conds.extend(self._time_conditions(since, until))
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT * FROM memories FINAL WHERE {where} "
            f"ORDER BY created_at DESC LIMIT {limit}"
        )
        return [self._row_to_memory(r) for r in rows]

    def search_by_vector(
        self,
        query_vec: list[float],
        layer: str,
        *,
        limit: int = 200,
        since: str | None = None,
        until: str | None = None,
    ) -> list[Memory]:
        """Pre-filter candidates by cosineDistance at the SQL level.

        Much more efficient than loading all rows into Python — ClickHouse
        computes the distance over the full table and returns only the
        closest ``limit`` rows.
        """
        vec_literal = self._float_array_literal(query_vec)
        conds = [
            f"layer = '{self._escape(layer)}'",
            "is_active = 1",
            "length(embedding) > 0",
        ]
        conds.extend(self._time_conditions(since, until))
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT *, cosineDistance(embedding, {vec_literal}) AS _dist "
            f"FROM memories FINAL WHERE {where} "
            f"ORDER BY _dist ASC LIMIT {limit}"
        )
        return [self._row_to_memory(r) for r in rows]

    def list_memories(
        self,
        *,
        layer: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "created_at",
        since: str | None = None,
        until: str | None = None,
    ) -> list[Memory]:
        """Flexible memory listing with optional filters, pagination, and sorting."""
        conds = ["is_active = 1"]
        if layer:
            conds.append(f"layer = '{self._escape(layer)}'")
        if category:
            conds.append(f"category = '{self._escape(category)}'")
        conds.extend(self._time_conditions(since, until))
        sort_col = sort_by if sort_by in ("created_at", "accessed_at", "updated_at", "access_count") else "created_at"
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT * FROM memories FINAL WHERE {where} "
            f"ORDER BY {sort_col} DESC LIMIT {limit} OFFSET {offset}"
        )
        return [self._row_to_memory(r) for r in rows]

    def count(self) -> int:
        rows = self._query_json(
            "SELECT count() as cnt FROM memories FINAL WHERE is_active = 1"
        )
        return int(rows[0]["cnt"]) if rows else 0

    def count_by_layer(self) -> dict[str, int]:
        rows = self._query_json(
            "SELECT layer, count() as cnt FROM memories FINAL "
            "WHERE is_active = 1 GROUP BY layer"
        )
        result = {"working": 0, "episodic": 0, "semantic": 0}
        for r in rows:
            result[r["layer"]] = int(r["cnt"])
        return result

    def stats(self) -> dict:
        rows = self._query_json(
            "SELECT layer, category, count() as cnt FROM memories FINAL "
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
            f"SELECT * FROM memories FINAL "
            f"WHERE hasAny(tags, {tags_lit}) AND is_active = 1"
        )
        return [self._row_to_memory(r) for r in rows]

    def find_stale_episodic(self, decay_days: int = 120) -> list[Memory]:
        rows = self._query_json(
            f"SELECT * FROM memories FINAL "
            f"WHERE layer = 'episodic' AND is_active = 1 "
            f"AND access_count = 0 "
            f"AND accessed_at < now() - INTERVAL {decay_days} DAY"
        )
        return [self._row_to_memory(r) for r in rows]

    def find_deleted(self, days: int = 7) -> list[Memory]:
        rows = self._query_json(
            f"SELECT * FROM memories FINAL "
            f"WHERE is_active = 0 "
            f"AND updated_at < now() - INTERVAL {days} DAY"
        )
        return [self._row_to_memory(r) for r in rows]

    def get_episodic_by_month(self, month: str) -> list[Memory]:
        # month format: "2026-01"
        rows = self._query_json(
            f"SELECT * FROM memories FINAL "
            f"WHERE layer = 'episodic' AND is_active = 1 "
            f"AND formatDateTime(created_at, '%Y-%m') = '{self._escape(month)}' "
            f"ORDER BY created_at"
        )
        return [self._row_to_memory(r) for r in rows]

    def get_tag_frequencies(self, layer: str = "episodic", min_count: int = 3) -> dict[str, int]:
        rows = self._query_json(
            f"SELECT tag, count() as cnt FROM ("
            f"  SELECT arrayJoin(tags) as tag FROM memories FINAL "
            f"  WHERE layer = '{self._escape(layer)}' AND is_active = 1"
            f") GROUP BY tag HAVING cnt >= {min_count} ORDER BY cnt DESC"
        )
        return {r["tag"]: int(r["cnt"]) for r in rows}

    # -- Raw Transcripts ---------------------------------------------------

    def insert_raw(self, session_id: str, source: str, content: str) -> str:
        """Insert a raw transcript. Returns the generated id."""
        raw_id = str(uuid.uuid4())
        now = self._now_str()
        char_count = len(content)
        sql = (
            f"INSERT INTO raw_transcripts (id, session_id, source, content, "
            f"char_count, is_processed, created_at) VALUES "
            f"('{self._escape(raw_id)}', '{self._escape(session_id)}', "
            f"'{self._escape(source)}', '{self._escape(content)}', "
            f"{char_count}, 0, '{now}')"
        )
        self._session.query(sql)
        return raw_id

    def get_raw(self, raw_id: str) -> dict | None:
        rows = self._query_json(
            f"SELECT * FROM raw_transcripts "
            f"WHERE id = '{self._escape(raw_id)}' LIMIT 1"
        )
        return rows[0] if rows else None

    def list_unprocessed_raw(self, limit: int = 100) -> list[dict]:
        return self._query_json(
            f"SELECT * FROM raw_transcripts "
            f"WHERE is_processed = 0 "
            f"ORDER BY created_at ASC LIMIT {limit}"
        )

    def mark_raw_processed(self, raw_id: str) -> None:
        now = self._now_str()
        self._session.query(
            f"ALTER TABLE raw_transcripts UPDATE "
            f"is_processed = 1, processed_at = '{now}' "
            f"WHERE id = '{self._escape(raw_id)}'"
        )

    def reset_orphan_processed_raws(self) -> int:
        """Find processed raw_transcripts that produced no entities and reset them.

        This fixes the bug where mark_raw_processed was called even on failed
        extraction, permanently losing the data.  Resetting lets the refinement
        loop retry.
        """
        # Find processed raws whose id does NOT appear as raw_id in episodes
        rows = self._query_json(
            "SELECT id FROM raw_transcripts "
            "WHERE is_processed = 1 "
            "AND id NOT IN (SELECT DISTINCT raw_id FROM episodes WHERE raw_id != '')"
        )
        if not rows:
            return 0
        for row in rows:
            rid = self._escape(row["id"])
            self._session.query(
                f"ALTER TABLE raw_transcripts UPDATE "
                f"is_processed = 0, processed_at = '' "
                f"WHERE id = '{rid}'"
            )
        return len(rows)

    def count_raw(self) -> dict:
        rows = self._query_json(
            "SELECT "
            "count() as total, "
            "countIf(is_processed = 1) as processed, "
            "countIf(is_processed = 0) as unprocessed "
            "FROM raw_transcripts"
        )
        if not rows:
            return {"total": 0, "processed": 0, "unprocessed": 0}
        r = rows[0]
        return {
            "total": int(r["total"]),
            "processed": int(r["processed"]),
            "unprocessed": int(r["unprocessed"]),
        }

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
