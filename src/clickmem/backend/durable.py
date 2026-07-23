"""Durable backend — clickmem memory on a `chdb.durable` object.

Same `query` / `execute` / `vector_search` / `close` contract as the local
chDB backend, but the memory database lives in a Durable Analytical Object:
its state is checkpointed to object storage you own (S3 / GCS / Azure Blob /
any S3-compatible / a local folder), single-writer, and portable across hosts.
That's the third tier alongside `LocalBackend` (ephemeral, in-process) and
`ClickHouseBackend` (needs a server): durable, but serverless.

Selected by `CLICKMEM_BACKEND=durable` with `CLICKMEM_DURABLE_URL` (the object
store) and `CLICKMEM_DURABLE_ID` (this memory's object id).
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from clickmem.backend.local_chdb import _bytes_to_str, _parse_json_each_row
from clickmem.schema import bootstrap_statements
from clickmem.sqlutil import quote_array_float

_log = logging.getLogger(__name__)


class DurableBackend:
    """chDB memory whose authoritative state is a durable object."""

    def __init__(self, url: str, oid: str, embed_dim: int = 256,
                 db: str = "clickmem", owner: Optional[str] = None) -> None:
        from chdb import durable as cd

        self.embed_dim = embed_dim
        self._db = db
        self._ns = cd.Namespace(url, owner=owner or "clickmem", db=db)
        self._obj = self._ns.open(oid)
        # clickmem tables are unqualified, so they resolve to the session's
        # current database — point it at the DAO's db, which its checkpoint /
        # restore captures. Run via query() so it is not written to the WAL.
        self._obj.query(f"USE {db}")
        self._bootstrap()

    def _run_ddl(self, sql: str) -> None:
        # Idempotent schema, re-run on every open — keep it OUT of the WAL
        # (it's already in the checkpointed base after the first run).
        self._obj.query(sql)

    def _bootstrap(self) -> None:
        for stmt in bootstrap_statements(self.embed_dim):
            try:
                self._run_ddl(stmt)
            except Exception as e:  # tolerate optional pieces (e.g. an ANN index)
                _log.debug("durable bootstrap skipped: %s", str(e).splitlines()[0][:120])

    # -- Backend protocol -------------------------------------------------
    def query(self, sql: str) -> List[dict[str, Any]]:
        return _parse_json_each_row(_bytes_to_str(self._obj.query(sql, "JSONEachRow")))

    def execute(self, sql: str) -> None:
        # a committed write is durable at return: logged to the WAL, then flushed
        self._obj.execute(sql)
        self._obj.flush()

    def vector_search(self, table: str, query_vec: List[float], where: str, limit: int,
                      embedding_column: str = "embedding", select: str = "*",
                      order_extra: str = "") -> List[dict[str, Any]]:
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
        try:
            self._obj.checkpoint()  # fold the WAL into a fresh base before release
        finally:
            self._obj.close()
