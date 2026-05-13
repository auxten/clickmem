"""Event log writer.

Every write API (REST, MCP, CLI) appends one row to ``events``. The dashboard
activity feed and per-integration health sparklines read straight from this
table.

Event ``kind`` values used across the code:
    memory.expand, memory.revise, memory.contract, memory.pin, memory.unpin,
    memory.bulk, memory.resolve_conflict,
    blacklist.add, blacklist.remove, blacklist.hit,
    raw.land, recall.run, project.link, project.create,
    agent.install, agent.uninstall, agent.test,
    server.start, server.error
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List

from clickmem.backend import Backend, get_backend
from clickmem.sqlutil import quote_str, utc_now_sql


_log = logging.getLogger(__name__)


def write(
    kind: str,
    *,
    agent: str = "",
    project_id: str = "",
    memory_id: str = "",
    message: str = "",
    payload: Dict[str, Any] | None = None,
    backend: Backend | None = None,
) -> None:
    """Append one row to the events table. Errors are logged but never raised."""
    backend = backend or get_backend()
    payload_str = json.dumps(payload, ensure_ascii=False) if payload else ""
    eid = uuid.uuid4().hex
    sql = (
        "INSERT INTO events "
        "(id, kind, agent, project_id, memory_id, message, payload_json, created_at) VALUES ("
        f"{quote_str(eid)}, {quote_str(kind)}, {quote_str(agent)}, "
        f"{quote_str(project_id)}, {quote_str(memory_id)}, {quote_str(message)}, "
        f"{quote_str(payload_str)}, {utc_now_sql()}"
        ")"
    )
    try:
        backend.execute(sql)
    except Exception as e:  # never block the caller on the audit log
        _log.warning("event.write failed (%s): %s", kind, e)


def list_events(
    since: str | None = None,
    kind: str | None = None,
    limit: int = 200,
    agent: str | None = None,
    backend: Backend | None = None,
) -> List[dict[str, Any]]:
    backend = backend or get_backend()
    where: list[str] = []
    if since:
        where.append(f"created_at >= parseDateTime64BestEffortOrNull({quote_str(since)})")
    if kind:
        where.append(f"kind = {quote_str(kind)}")
    if agent:
        where.append(f"agent = {quote_str(agent)}")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    sql = (
        "SELECT id, kind, agent, project_id, memory_id, message, payload_json, "
        "toString(created_at) AS created_at FROM events "
        f"{clause} ORDER BY created_at DESC LIMIT {int(limit)}"
    )
    rows = backend.query(sql)
    for r in rows:
        raw = r.get("payload_json") or ""
        if raw:
            try:
                r["payload"] = json.loads(raw)
            except Exception:
                r["payload"] = {}
        else:
            r["payload"] = {}
    return rows


def activity_counts(
    hours: int = 24,
    bucket_minutes: int = 60,
    agent: str | None = None,
    kind: str | None = None,
    backend: Backend | None = None,
) -> List[dict[str, Any]]:
    """Return time-bucketed event counts for sparklines."""
    backend = backend or get_backend()
    where = [
        f"created_at >= now() - INTERVAL {int(hours)} HOUR",
    ]
    if agent:
        where.append(f"agent = {quote_str(agent)}")
    if kind:
        where.append(f"kind = {quote_str(kind)}")
    clause = "WHERE " + " AND ".join(where)
    sql = (
        f"SELECT toStartOfInterval(created_at, INTERVAL {int(bucket_minutes)} MINUTE) AS bucket, "
        "count() AS c FROM events "
        f"{clause} GROUP BY bucket ORDER BY bucket ASC"
    )
    rows = backend.query(sql)
    out: List[dict[str, Any]] = []
    for r in rows:
        out.append({"bucket": str(r.get("bucket")), "count": int(r.get("c", 0) or 0)})
    return out
