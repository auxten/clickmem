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

from clickmem.schema import ann_index_statements, bootstrap_statements
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
            self.execute(stmt)

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
