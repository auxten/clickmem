"""Memory history: append-only log + diff helpers."""

from __future__ import annotations

import difflib
from typing import Any, List

from clickmem.backend import Backend, get_backend
from clickmem.models import MemoryHistoryEntry
from clickmem.sqlutil import quote_str, utc_now_sql


def append(
    memory_id: str,
    op: str,
    content: str,
    edited_by: str = "",
    prev_id: str = "",
    note: str = "",
    backend: Backend | None = None,
) -> int:
    """Append one history row. Returns the new version number."""
    backend = backend or get_backend()
    rows = backend.query(
        f"SELECT max(version) AS v FROM memory_history WHERE memory_id = {quote_str(memory_id)}"
    )
    cur = 0
    if rows:
        try:
            cur = int(rows[0].get("v") or 0)
        except Exception:
            cur = 0
    version = cur + 1
    sql = (
        "INSERT INTO memory_history "
        "(memory_id, version, op, content, edited_by, edited_at, prev_id, note) VALUES ("
        f"{quote_str(memory_id)}, {version}, {quote_str(op)}, {quote_str(content)}, "
        f"{quote_str(edited_by)}, {utc_now_sql()}, {quote_str(prev_id)}, {quote_str(note)}"
        ")"
    )
    backend.execute(sql)
    return version


def get_history(memory_id: str, backend: Backend | None = None) -> List[MemoryHistoryEntry]:
    backend = backend or get_backend()
    sql = (
        "SELECT memory_id, version, op, content, edited_by, "
        "toString(edited_at) AS edited_at, prev_id, note FROM memory_history "
        f"WHERE memory_id = {quote_str(memory_id)} ORDER BY version ASC"
    )
    rows = backend.query(sql)
    return [MemoryHistoryEntry.from_row(r) for r in rows]


def diff(a: str, b: str, n: int = 3) -> List[str]:
    """Unified diff between two content strings."""
    a_lines = (a or "").splitlines(keepends=False)
    b_lines = (b or "").splitlines(keepends=False)
    return list(
        difflib.unified_diff(a_lines, b_lines, fromfile="before", tofile="after", n=n, lineterm="")
    )


def history_with_diffs(memory_id: str, backend: Backend | None = None) -> List[dict[str, Any]]:
    """Return history entries each annotated with a unified diff from prev."""
    entries = get_history(memory_id, backend=backend)
    out: List[dict[str, Any]] = []
    prev = ""
    for e in entries:
        item = e.to_dict()
        item["diff"] = diff(prev, e.content) if prev else []
        out.append(item)
        prev = e.content
    return out
