"""Append-only raw transcript landing.

Hooks fire-and-forget POST ``/v1/raw`` and the server simply inserts. Dedup
is by ``(session_id, sha256(text))`` and is enforced on the insert path. This
table is **never** read by recall.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, List

from clickmem.backend import Backend, get_backend
from clickmem.events import write as event_write
from clickmem.sqlutil import quote_str, utc_now_sql


_log = logging.getLogger(__name__)


def append(
    text: str,
    *,
    session_id: str,
    agent: str = "",
    project_id: str = "",
    role: str = "",
    meta: dict[str, Any] | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Insert one raw row, deduped by (session_id, sha256(text))."""
    backend = backend or get_backend()
    text = text or ""
    if not text.strip():
        return {"ok": True, "skipped": "empty"}
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    dup = backend.query(
        "SELECT id FROM raw_transcripts "
        f"WHERE session_id = {quote_str(session_id)} AND text_hash = {quote_str(text_hash)} LIMIT 1"
    )
    if dup:
        return {"ok": True, "skipped": "duplicate", "id": dup[0].get("id", "")}

    rid = uuid.uuid4().hex
    meta_str = json.dumps(meta or {}, ensure_ascii=False)
    sql = (
        "INSERT INTO raw_transcripts "
        "(id, session_id, agent, project_id, role, text, text_hash, meta_json, created_at) VALUES ("
        f"{quote_str(rid)}, {quote_str(session_id)}, {quote_str(agent)}, "
        f"{quote_str(project_id)}, {quote_str(role)}, {quote_str(text)}, "
        f"{quote_str(text_hash)}, {quote_str(meta_str)}, {utc_now_sql()}"
        ")"
    )
    backend.execute(sql)
    event_write(
        "raw.land",
        agent=agent,
        project_id=project_id,
        message="raw appended",
        payload={"session_id": session_id, "len": len(text)},
        backend=backend,
    )
    return {"ok": True, "id": rid}


def get_raw(
    session_id: str | None = None,
    last: int = 50,
    agent: str | None = None,
    backend: Backend | None = None,
) -> List[dict[str, Any]]:
    backend = backend or get_backend()
    where: list[str] = []
    if session_id:
        where.append(f"session_id = {quote_str(session_id)}")
    if agent:
        where.append(f"agent = {quote_str(agent)}")
    clause = "WHERE " + " AND ".join(where) if where else ""
    sql = (
        "SELECT id, session_id, agent, project_id, role, text, "
        "toString(created_at) AS created_at, meta_json FROM raw_transcripts "
        f"{clause} ORDER BY created_at DESC LIMIT {int(last)}"
    )
    rows = backend.query(sql)
    for r in rows:
        raw = r.get("meta_json") or ""
        if raw:
            try:
                r["meta"] = json.loads(raw)
            except Exception:
                r["meta"] = {}
        else:
            r["meta"] = {}
    return rows
