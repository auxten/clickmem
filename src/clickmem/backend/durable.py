"""Durable backend — clickmem memory on a `chdb.durable` object.

Same `query` / `execute` / `vector_search` / `close` contract as the local
chDB backend, but the memory database lives in a Durable Analytical Object:
its state is checkpointed to object storage you own (S3 / GCS / Azure Blob /
any S3-compatible / a local folder), single-writer, and portable across hosts.
That's the third tier alongside `LocalBackend` (persistent but bound to one
machine) and `ClickHouseBackend` (needs a server): durable, portable,
serverless.

Selected by `CLICKMEM_BACKEND=durable` with `CLICKMEM_DURABLE_URL` (the object
store) and `CLICKMEM_DURABLE_ID` (this memory's object id).

Single-writer lease: one live process owns an object at a time. If the
previous process crashed without releasing (no `close()`), reopening raises
`chdb.durable.LeaseError` until its lease expires (~60s). Either wait it out
or, once you are sure the old process is dead, construct with `force=True`
to take over immediately — a fenced-off old writer can no longer commit, so
takeover is safe.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional

from clickmem.backend.local_chdb import _bytes_to_str, _parse_json_each_row
from clickmem.schema import bootstrap_statements
from clickmem.sqlutil import quote_array_float

_log = logging.getLogger(__name__)


class DurableBackend:
    """chDB memory whose authoritative state is a durable object."""

    def __init__(self, url: str, oid: str, embed_dim: int = 256,
                 db: str = "clickmem", owner: Optional[str] = None,
                 force: bool = False) -> None:
        from chdb import durable as cd

        self.embed_dim = embed_dim
        self._db = db
        # serialize all object access: the server's background worker and
        # request handlers share this backend across threads
        self._lock = threading.RLock()
        self._lazy_bytes = 0            # deferred audit bytes since last flush
        self._lazy_since: Optional[float] = None  # when the oldest of those landed
        # statement bytes since the last checkpoint (approximates WAL size;
        # segments already on storage from a previous run are covered by the
        # segment-count trigger instead)
        self._wal_bytes = 0
        self._ns = cd.Namespace(url, owner=owner or "clickmem", db=db)
        try:
            self._obj = self._ns.open(oid, force=force)
        except cd.LeaseError as e:
            raise cd.LeaseError(
                f"memory object {oid!r} is lease-locked ({e}). Another clickmem "
                f"process may be using it — or a previous one crashed without "
                f"closing. Wait for the lease to expire (~60s), or pass "
                f"force=True once you are sure the old process is dead."
            ) from e
        # clickmem tables are unqualified, so they resolve to the session's
        # current database — point the live session at the DAO's db. Run via
        # query() so it is not written to the WAL: replay restores this context
        # itself (chdb.durable replays with the manifest's db as current).
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

    # -- growth control thresholds ----------------------------------------
    # Maintenance always rides normal operations — no dedicated timer thread
    # (same pattern as the lease, which renews inside execute near expiry).
    #
    # fold the WAL into a fresh base once either trips, so a long-running
    # service doesn't leave an ever-longer replay chain for the next open
    # (checkpoint is ~tens of ms at memory-store scale):
    _CHECKPOINT_SEGMENTS = 64       # many small belief writes
    _CHECKPOINT_WAL_BYTES = 32 << 20  # few but transcript-heavy segments
    # on close, folding is only worth a full base upload if the WAL grew past
    # this; below it, leave the few segments for the next open to replay
    # (also avoids minting an orphaned base on every service restart)
    _CLOSE_CHECKPOINT_SEGMENTS = 8
    # ship deferred audit writes once either trips: bytes bound the eventual
    # segment size, the deadline bounds how much a crash can lose in time
    _LAZY_FLUSH_BYTES = 1 << 20
    _LAZY_FLUSH_SECONDS = 60.0

    def _maybe_checkpoint(self) -> None:
        if (len(self._obj.wal) >= self._CHECKPOINT_SEGMENTS
                or self._wal_bytes >= self._CHECKPOINT_WAL_BYTES):
            self._obj.checkpoint()
            self._wal_bytes = 0

    def _flush_lazy(self) -> None:
        self._obj.flush()
        self._wal_bytes += self._lazy_bytes
        self._lazy_bytes = 0
        self._lazy_since = None
        self._maybe_checkpoint()

    def maybe_flush(self) -> None:
        # deadline check for the deferred audit tail; ridden by queries and
        # the server's background worker so even an idle tail ships within
        # ~_LAZY_FLUSH_SECONDS
        with self._lock:
            if (self._lazy_since is not None
                    and time.monotonic() - self._lazy_since >= self._LAZY_FLUSH_SECONDS):
                self._flush_lazy()

    # -- Backend protocol -------------------------------------------------
    def query(self, sql: str) -> List[dict[str, Any]]:
        with self._lock:
            self.maybe_flush()
            return _parse_json_each_row(_bytes_to_str(self._obj.query(sql, "JSONEachRow")))

    def execute(self, sql: str) -> None:
        # a committed write is durable at return: logged to the WAL, then flushed
        with self._lock:
            self._obj.execute(sql)
            self._obj.flush()
            # the flush carried any deferred audit writes too
            self._wal_bytes += self._lazy_bytes + len(sql)
            self._lazy_bytes = 0
            self._lazy_since = None
            self._maybe_checkpoint()

    def execute_lazy(self, sql: str) -> None:
        # audit-class write: applied to the local replica now, uploaded with
        # the next belief write / flush deadline / close. Keeps hot paths
        # (recall audit trail, raw transcript capture) free of upload latency;
        # a crash loses only the audit tail since the last flush, never a belief.
        with self._lock:
            self._obj.execute(sql)
            self._lazy_bytes += len(sql)
            if self._lazy_since is None:
                self._lazy_since = time.monotonic()
            if (self._lazy_bytes >= self._LAZY_FLUSH_BYTES
                    or time.monotonic() - self._lazy_since >= self._LAZY_FLUSH_SECONDS):
                self._flush_lazy()

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
        with self._lock:
            try:
                self._obj.flush()  # ship the audit tail; beliefs are already out
                if len(self._obj.wal) >= self._CLOSE_CHECKPOINT_SEGMENTS:
                    self._obj.checkpoint()  # fold only when replay would be long
            finally:
                self._obj.close()
