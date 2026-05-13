"""Dashboard stats: small SQL aggregations used by every Overview card."""

from __future__ import annotations

from typing import Any, List

from clickmem.backend import Backend, get_backend


def overview(backend: Backend | None = None) -> dict[str, Any]:
    backend = backend or get_backend()
    rows = backend.query(
        "SELECT count() AS total, "
        "sumIf(1, status = 'active') AS active, "
        "sumIf(1, pinned = 1) AS pinned, "
        "sumIf(1, status = 'conflicted') AS conflicted, "
        "sumIf(1, status = 'contracted') AS contracted "
        "FROM memories FINAL"
    )
    base = rows[0] if rows else {"total": 0, "active": 0, "pinned": 0, "conflicted": 0, "contracted": 0}
    last7 = backend.query(
        "SELECT count() AS c FROM memories FINAL "
        "WHERE created_at >= now() - INTERVAL 7 DAY"
    )
    prev7 = backend.query(
        "SELECT count() AS c FROM memories FINAL "
        "WHERE created_at >= now() - INTERVAL 14 DAY "
        "AND created_at < now() - INTERVAL 7 DAY"
    )
    raw_total = backend.query("SELECT count() AS c FROM raw_transcripts")
    events_24h = backend.query(
        "SELECT count() AS c FROM events WHERE created_at >= now() - INTERVAL 24 HOUR"
    )
    return {
        "total": int(base.get("total", 0) or 0),
        "active": int(base.get("active", 0) or 0),
        "pinned": int(base.get("pinned", 0) or 0),
        "conflicted": int(base.get("conflicted", 0) or 0),
        "contracted": int(base.get("contracted", 0) or 0),
        "last7": int(last7[0]["c"]) if last7 else 0,
        "prev7": int(prev7[0]["c"]) if prev7 else 0,
        "raw_transcripts": int(raw_total[0]["c"]) if raw_total else 0,
        "events_24h": int(events_24h[0]["c"]) if events_24h else 0,
    }


def by_projects(backend: Backend | None = None) -> List[dict[str, Any]]:
    backend = backend or get_backend()
    rows = backend.query(
        "SELECT project_id, count() AS memories, "
        "sumIf(1, pinned = 1) AS pinned, "
        "sumIf(1, status = 'conflicted') AS conflicts, "
        "toString(max(updated_at)) AS last_updated "
        "FROM memories FINAL WHERE status != 'contracted' "
        "GROUP BY project_id ORDER BY memories DESC LIMIT 50"
    )
    return rows


def by_kinds(backend: Backend | None = None) -> List[dict[str, Any]]:
    backend = backend or get_backend()
    rows = backend.query(
        "SELECT kind, count() AS c FROM memories FINAL "
        "WHERE status != 'contracted' GROUP BY kind ORDER BY c DESC"
    )
    return rows


def privacy_mix(backend: Backend | None = None) -> List[dict[str, Any]]:
    backend = backend or get_backend()
    rows = backend.query(
        "SELECT project_id, privacy, count() AS c FROM memories FINAL "
        "WHERE status != 'contracted' "
        "GROUP BY project_id, privacy ORDER BY c DESC"
    )
    return rows
