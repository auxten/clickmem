"""Refused patterns: enforced on insert AND on recall.

Patterns are case-insensitive substring matches, or the exact-match form
``id:<uuid>`` to ban a specific memory id from ever being recalled.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, List

from clickmem.backend import Backend, get_backend
from clickmem.models import Blacklist
from clickmem.sqlutil import quote_str, utc_now_sql


_log = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex


def add(pattern: str, scope: str = "global", reason: str = "", backend: Backend | None = None) -> Blacklist:
    backend = backend or get_backend()
    if not pattern or not pattern.strip():
        raise ValueError("blacklist pattern cannot be empty")
    pattern = pattern.strip()
    scope = scope or "global"
    bid = _new_id()
    sql = (
        "INSERT INTO blacklist (id, pattern, scope, reason, hit_count, created_at, updated_at) VALUES ("
        f"{quote_str(bid)}, {quote_str(pattern)}, {quote_str(scope)}, {quote_str(reason)}, 0, "
        f"{utc_now_sql()}, {utc_now_sql()}"
        ")"
    )
    backend.execute(sql)
    return Blacklist.from_row(
        {
            "id": bid,
            "pattern": pattern,
            "scope": scope,
            "reason": reason,
            "hit_count": 0,
            "created_at": "",
            "updated_at": "",
        }
    )


def remove(blacklist_id: str, backend: Backend | None = None) -> bool:
    backend = backend or get_backend()
    sql = f"ALTER TABLE blacklist DELETE WHERE id = {quote_str(blacklist_id)}"
    backend.execute(sql)
    return True


def list_all(backend: Backend | None = None) -> List[Blacklist]:
    backend = backend or get_backend()
    rows = backend.query(
        "SELECT id, pattern, scope, reason, hit_count, "
        "toString(created_at) AS created_at, toString(updated_at) AS updated_at "
        "FROM blacklist FINAL ORDER BY created_at DESC"
    )
    return [Blacklist.from_row(r) for r in rows]


def patterns(scope: str | None = None, backend: Backend | None = None) -> List[Blacklist]:
    items = list_all(backend=backend)
    if scope:
        items = [b for b in items if not b.scope or b.scope == "global" or b.scope == scope]
    return items


def enforce_on_insert(
    content: str,
    project_id: str = "",
    backend: Backend | None = None,
) -> Blacklist | None:
    """Return the first matching blacklist entry, or ``None``."""
    if not content:
        return None
    scopes = ("global", project_id) if project_id else ("global",)
    for entry in list_all(backend=backend):
        if entry.scope not in scopes and entry.scope != "global":
            continue
        if _match(entry.pattern, content, None):
            _increment_hit(entry.id, backend=backend)
            return entry
    return None


def enforce_on_recall(
    hits: List[dict[str, Any]],
    project_id: str = "",
    backend: Backend | None = None,
) -> List[dict[str, Any]]:
    """Filter recall hits in place; bumps hit_count when something gets dropped."""
    entries = list_all(backend=backend)
    if not entries:
        return hits
    kept: List[dict[str, Any]] = []
    for h in hits:
        memory_id = str(h.get("id", ""))
        text = str(h.get("content", ""))
        rejected_by: Blacklist | None = None
        for e in entries:
            if e.scope not in ("global", project_id):
                continue
            if _match(e.pattern, text, memory_id):
                rejected_by = e
                break
        if rejected_by is not None:
            _increment_hit(rejected_by.id, backend=backend)
            continue
        kept.append(h)
    return kept


def _match(pattern: str, content: str, memory_id: str | None) -> bool:
    p = (pattern or "").strip()
    if not p:
        return False
    if p.lower().startswith("id:") and memory_id is not None:
        return p[3:].strip() == memory_id
    return p.lower() in (content or "").lower()


def _increment_hit(blacklist_id: str, backend: Backend | None = None) -> None:
    backend = backend or get_backend()
    sql = (
        "INSERT INTO blacklist "
        "(id, pattern, scope, reason, hit_count, created_at, updated_at) "
        f"SELECT id, pattern, scope, reason, hit_count + 1, created_at, {utc_now_sql()} "
        f"FROM blacklist FINAL WHERE id = {quote_str(blacklist_id)}"
    )
    try:
        backend.execute(sql)
    except Exception as e:
        _log.debug("hit_count bump failed: %s", e)
