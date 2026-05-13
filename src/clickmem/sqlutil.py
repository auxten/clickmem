"""Tiny SQL helpers shared by both storage backends.

Nothing in here imports chdb or clickhouse-connect; both backends call into
these helpers when building DDL/DML strings.
"""

from __future__ import annotations

from typing import Iterable, Sequence


def quote_str(value: str | None) -> str:
    """Render a Python string as a SQL string literal safe for ClickHouse."""
    if value is None:
        return "''"
    s = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def quote_bool(value: bool) -> str:
    return "1" if bool(value) else "0"


def quote_array_str(values: Iterable[str] | None) -> str:
    if not values:
        return "[]"
    inner = ", ".join(quote_str(v) for v in values)
    return f"[{inner}]"


def quote_array_float(values: Sequence[float] | None) -> str:
    if not values:
        return "[]"
    inner = ", ".join(_float_repr(v) for v in values)
    return f"[{inner}]"


def _float_repr(v: float) -> str:
    if v != v:  # NaN
        return "0"
    return f"{float(v):.7f}"


def vector_cast(values: Sequence[float], dim: int) -> str:
    """Cast a Python float list to ClickHouse Array(Float32) literal."""
    arr = quote_array_float(values[:dim] if values else [0.0] * dim)
    return f"CAST({arr} AS Array(Float32))"


def multi_search_any_ci(column: str, needles: Sequence[str]) -> str:
    """Build ``multiSearchAnyCaseInsensitive(<column>, [...])`` SQL fragment."""
    if not needles:
        return "1"
    arr = quote_array_str(needles)
    return f"multiSearchAnyCaseInsensitive({column}, {arr})"


def utc_now_sql() -> str:
    return "now64(3, 'UTC')"
