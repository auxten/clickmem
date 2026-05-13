"""ClickHouse Cloud / self-hosted backend via ``clickhouse-connect``.

Selected when ``CLICKMEM_BACKEND=clickhouse``. Configuration reads:

    CLICKMEM_CH_URL        e.g. https://abc.clickhouse.cloud:8443
    CLICKMEM_CH_USER       default 'default'
    CLICKMEM_CH_PASSWORD
    CLICKMEM_CH_DATABASE   default 'clickmem'
"""

from __future__ import annotations

import logging
from typing import Any, List
from urllib.parse import urlparse

from clickmem.schema import ann_index_statements, bootstrap_statements
from clickmem.sqlutil import quote_array_float


_log = logging.getLogger(__name__)


class ClickHouseBackend:
    """HTTP-based ClickHouse backend."""

    def __init__(
        self,
        url: str | None,
        user: str | None,
        password: str | None,
        database: str = "clickmem",
        embed_dim: int = 256,
        ann_index: bool = True,
    ) -> None:
        if not url:
            raise RuntimeError(
                "CLICKMEM_BACKEND=clickhouse requires CLICKMEM_CH_URL to be set"
            )
        self.url = url
        self.user = user or "default"
        self.password = password or ""
        self.database = database or "clickmem"
        self.embed_dim = embed_dim
        self._client = None
        self._open_client()
        self._bootstrap()
        if ann_index:
            for stmt in ann_index_statements():
                try:
                    self.execute(stmt)
                except Exception as e:
                    _log.debug("ANN index add skipped on ClickHouse: %s", e)

    def _open_client(self) -> None:
        import clickhouse_connect  # type: ignore

        parsed = urlparse(self.url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (8443 if parsed.scheme == "https" else 8123)
        secure = parsed.scheme == "https"

        client = clickhouse_connect.get_client(
            host=host,
            port=port,
            username=self.user,
            password=self.password,
            secure=secure,
        )
        client.command(f"CREATE DATABASE IF NOT EXISTS {self.database}")
        client.command(f"USE {self.database}")
        self._client = client

    def _bootstrap(self) -> None:
        for stmt in bootstrap_statements(self.embed_dim):
            self.execute(stmt)

    def query(self, sql: str) -> List[dict[str, Any]]:
        assert self._client is not None
        res = self._client.query(sql)
        columns = res.column_names
        rows = res.result_rows
        return [dict(zip(columns, row)) for row in rows]

    def execute(self, sql: str) -> None:
        assert self._client is not None
        self._client.command(sql)

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
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass
