"""Storage-backend abstraction.

Two concrete implementations live alongside this module:
- ``local_chdb`` wraps a persistent ``chdb.session.Session``.
- ``clickhouse`` uses ``clickhouse-connect`` against ClickHouse Cloud or any
  self-hosted ClickHouse over HTTP.

Everything else in the codebase depends only on the ``Backend`` protocol and
the ``get_backend()`` factory. No module outside ``clickmem.backend.*`` is
allowed to import ``chdb`` or ``clickhouse-connect`` directly.
"""

from __future__ import annotations

from typing import Any, List, Protocol, runtime_checkable

from clickmem.config import get_config


@runtime_checkable
class Backend(Protocol):
    """Storage protocol implemented by both chDB and ClickHouse adapters."""

    def query(self, sql: str) -> List[dict[str, Any]]:
        """Run a SELECT and return rows as dicts."""
        ...

    def execute(self, sql: str) -> None:
        """Run an INSERT / ALTER / DDL statement (no results expected)."""
        ...

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
        """Return top-K rows ranked by cosine distance to ``query_vec``.

        Returned rows include a ``cosine_sim`` float column in [0, 1].
        """
        ...

    def close(self) -> None:
        ...


_singleton: Backend | None = None


def get_backend(refresh: bool = False) -> Backend:
    """Return the process-wide backend singleton chosen by ``CLICKMEM_BACKEND``."""
    global _singleton
    if _singleton is not None and not refresh:
        return _singleton

    cfg = get_config(refresh=refresh)
    choice = (cfg.backend or "local").lower()

    if choice in ("local", "chdb", "local_chdb"):
        from clickmem.backend.local_chdb import LocalBackend

        _singleton = LocalBackend(db_path=cfg.db_path, embed_dim=cfg.embedding_dim)
    elif choice in ("clickhouse", "ch", "cloud"):
        from clickmem.backend.clickhouse import ClickHouseBackend

        _singleton = ClickHouseBackend(
            url=cfg.ch_url,
            user=cfg.ch_user,
            password=cfg.ch_password,
            database=cfg.ch_database,
            embed_dim=cfg.embedding_dim,
        )
    else:
        raise ValueError(f"Unknown CLICKMEM_BACKEND: {choice!r}")

    return _singleton


def reset_backend() -> None:
    """Drop the cached backend instance. Tests use this between cases."""
    global _singleton
    if _singleton is not None:
        try:
            _singleton.close()
        except Exception:
            pass
    _singleton = None


__all__ = ["Backend", "get_backend", "reset_backend"]
