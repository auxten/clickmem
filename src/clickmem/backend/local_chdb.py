"""chDB (embedded ClickHouse) backend.

Wraps a single ``chdb.session.Session`` against a persistent on-disk database
under ``CLICKMEM_DB_PATH``. Multiple processes against the same path must
coordinate; we retry briefly on the well-known "single-instance lock" error
that chdb raises when another process holds the database open.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, List

from clickmem.schema import (
    MEMORIES_COLUMNS,
    ann_index_statements,
    bootstrap_statements,
    memories_ddl,
    memories_rekey_to_id_statements,
)
from clickmem.sqlutil import quote_array_float


_log = logging.getLogger(__name__)
_LOCK = threading.RLock()


class LocalBackend:
    """chDB-backed Backend implementation."""

    def __init__(self, db_path: Path, embed_dim: int = 256, ann_index: bool = False) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.embed_dim = embed_dim
        self._session = None
        self._open_session()
        self._bootstrap()
        if ann_index:
            # Best-effort; chDB versions vary on annoy availability.
            for stmt in ann_index_statements():
                try:
                    self.execute(stmt)
                except Exception as e:
                    _log.debug("ANN index add skipped: %s", e)

    def _open_session(self) -> None:
        import chdb  # noqa: F401  (imported here so the dependency is lazy)
        from chdb import session as chsession

        deadline = time.monotonic() + 10.0
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._session = chsession.Session(str(self.db_path))
                return
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                if "lock" in msg or "in use" in msg or "another instance" in msg:
                    time.sleep(0.25)
                    continue
                raise
        raise RuntimeError(f"Could not open chDB at {self.db_path}: {last_err}")

    def _bootstrap(self) -> None:
        for stmt in bootstrap_statements(self.embed_dim):
            try:
                self.execute(stmt)
            except Exception as e:  # noqa: BLE001
                if _is_memories_create(stmt) and _is_table_exists_error(e):
                    self._attach_existing_memories_table()
                    continue
                raise
        self._migrate_memories_sorting_key()

    def _attach_existing_memories_table(self) -> None:
        """Recover chDB tables whose data directory exists but metadata is absent.

        chDB can leave ``data/default/memories`` behind while a new session's
        overlay metadata no longer lists the table. A plain CREATE then raises
        TABLE_ALREADY_EXISTS and the following ALTERs see UNKNOWN_TABLE. Attach
        with the old sorting key first so legacy indexes can still be read and
        migrated to the current id-only key.
        """
        if self._table_visible("memories"):
            return

        last_err: Exception | None = None
        for ddl in _memories_attach_candidates(self.embed_dim):
            try:
                self.execute(ddl)
                self.query("SELECT id, content, project_id, kind FROM memories LIMIT 1")
                return
            except Exception as e:  # noqa: BLE001
                last_err = e
                try:
                    self.execute("DETACH TABLE IF EXISTS memories")
                except Exception:
                    pass
        raise RuntimeError(f"Could not attach existing chDB memories table: {last_err}")

    def _table_visible(self, name: str) -> bool:
        try:
            rows = self.query(f"SELECT name FROM system.tables WHERE name = '{name}' LIMIT 1")
        except Exception:
            return False
        return bool(rows)

    def _migrate_memories_sorting_key(self) -> None:
        try:
            rows = self.query("SELECT sorting_key FROM system.tables WHERE name = 'memories' LIMIT 1")
            sorting_key = str(rows[0].get("sorting_key", "") if rows else "").replace("`", "").replace(" ", "")
        except Exception as e:  # noqa: BLE001
            _log.warning("could not inspect memories sorting key: %s", e)
            return
        if sorting_key in ("", "id"):
            return
        _log.warning("migrating memories sorting key from %s to id", sorting_key)
        for stmt in memories_rekey_to_id_statements(self.embed_dim):
            try:
                self.execute(stmt)
            except Exception as e:  # noqa: BLE001
                if _is_rename_table(stmt) and _is_rename_not_supported(e):
                    self._migrate_memories_sorting_key_without_rename()
                    return
                raise

    def _migrate_memories_sorting_key_without_rename(self) -> None:
        tmp = "memories__id_order_tmp"
        backup = "memories__project_kind_id_backup"
        self.execute(f"DROP TABLE IF EXISTS {backup}")
        self.execute(f"CREATE TABLE {backup} AS memories")
        self.execute(f"INSERT INTO {backup} SELECT * FROM memories")
        self.execute("DROP TABLE memories")
        self.execute(memories_ddl(self.embed_dim))
        self.execute(f"INSERT INTO memories ({MEMORIES_COLUMNS}) SELECT {MEMORIES_COLUMNS} FROM {tmp} FINAL")
        self.execute(f"DROP TABLE IF EXISTS {tmp}")

    def query(self, sql: str) -> List[dict[str, Any]]:
        with _LOCK:
            result = self._session.query(sql, "JSONEachRow")
        return _parse_json_each_row(_bytes_to_str(result))

    def execute(self, sql: str) -> None:
        with _LOCK:
            self._session.query(sql)

    def vector_search(
        self,
        table: str,
        query_vec: List[float],
        where: str,
        limit: int,
        embedding_column: str = "embedding",
        select: str = "*",
        order_extra: str = "",
    ) -> List[dict[str, Any]]:
        vec = quote_array_float(query_vec[: self.embed_dim] if query_vec else [0.0] * self.embed_dim)
        where_clause = f"WHERE {where}" if where and where.strip() else ""
        order_tail = f", {order_extra}" if order_extra else ""
        sql = (
            f"SELECT {select}, "
            f"1 - cosineDistance({embedding_column}, CAST({vec} AS Array(Float32))) AS cosine_sim "
            f"FROM {table} FINAL {where_clause} "
            f"ORDER BY cosine_sim DESC{order_tail} "
            f"LIMIT {int(limit)}"
        )
        return self.query(sql)

    def close(self) -> None:
        sess = self._session
        self._session = None
        if sess is None:
            return
        try:
            sess.close()
        except Exception:
            pass


def _bytes_to_str(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return raw
    # chdb returns a result object whose str() is the rendered text.
    try:
        return raw.bytes().decode("utf-8", errors="replace")
    except Exception:
        return str(raw)


def _parse_json_each_row(text: str) -> List[dict[str, Any]]:
    rows: List[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # Defensive: skip non-JSON debug noise rather than blow up the call.
            _log.debug("skipping non-JSON row: %r", line[:120])
            continue
    return rows


def _is_create_if_not_exists(sql: str) -> bool:
    return "CREATE TABLE IF NOT EXISTS" in " ".join(sql.upper().split())


def _is_memories_create(sql: str) -> bool:
    normalized = " ".join(sql.upper().split())
    return normalized.startswith("CREATE TABLE IF NOT EXISTS MEMORIES ")


def _is_table_exists_error(exc: Exception) -> bool:
    msg = str(exc)
    return "TABLE_ALREADY_EXISTS" in msg or "Directory for table data" in msg


def _is_rename_table(sql: str) -> bool:
    return " ".join(sql.upper().split()).startswith("RENAME TABLE ")


def _is_rename_not_supported(exc: Exception) -> bool:
    msg = str(exc)
    return "renameTable() is not supported" in msg or "NOT_IMPLEMENTED" in msg


def _memories_attach_candidates(embed_dim: int) -> list[str]:
    current = memories_ddl(embed_dim)
    old = current.replace("ORDER BY id", "ORDER BY (project_id, kind, id)", 1)
    return [
        old.replace("CREATE TABLE IF NOT EXISTS", "ATTACH TABLE", 1),
        current.replace("CREATE TABLE IF NOT EXISTS", "ATTACH TABLE", 1),
    ]
