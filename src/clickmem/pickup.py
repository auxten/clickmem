"""User-curated pickup inbox for AI-proposed memories.

Candidates are deliberately stored outside ``memories`` and never participate
in recall.  The only promotion path is :func:`accept`, which requires the
candidate id, its current version, and an explicit personal/project scope.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from clickmem.backend import Backend, get_backend
from clickmem.events import write as event_write
from clickmem.models import PickupCandidate, VALID_KINDS, VALID_PICKUP_STATUS
from clickmem.sqlutil import quote_array_str, quote_str, utc_now_sql


_SELECT = (
    "id, run_id, source_agent, session_id, source_hash, project_id, cwd, "
    "content, kind, tags, suggested_scope, reason, evidence, confidence, "
    "status, decision_reason, memory_ref, version, prompt_version, content_hash, "
    "toString(created_at) AS created_at, toString(updated_at) AS updated_at"
)
_accept_lock = threading.Lock()
_ingest_lock = threading.Lock()


class CandidateVersionConflict(RuntimeError):
    def __init__(self, candidate: PickupCandidate) -> None:
        self.candidate = candidate
        super().__init__(
            f"pickup candidate changed; expected its current version {candidate.version}"
        )


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def _candidate_id(
    source_agent: str,
    session_id: str,
    source_hash: str,
    content: str,
) -> str:
    identity = "\0".join(
        (source_agent.strip(), session_id.strip(), source_hash.strip(), content.strip())
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _coerce_scope(value: str) -> str:
    scope = (value or "personal").strip().lower()
    return scope if scope in {"personal", "project"} else "personal"


def _coerce_kind(value: str) -> str:
    kind = (value or "free").strip().lower()
    return kind if kind in VALID_KINDS else "free"


def _dt_sql(value: datetime) -> str:
    timestamp = value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"toDateTime64({quote_str(timestamp)}, 3, 'UTC')"


def get(candidate_id: str, backend: Backend | None = None) -> PickupCandidate | None:
    backend = backend or get_backend()
    rows = backend.query(
        f"SELECT {_SELECT} FROM pickup_candidates FINAL "
        f"WHERE id = {quote_str(candidate_id)} LIMIT 1"
    )
    return PickupCandidate.from_row(rows[0]) if rows else None


def list_candidates(
    *,
    status: str | None = "pending",
    project_id: str | None = None,
    source_agent: str | None = None,
    offset: int = 0,
    limit: int = 100,
    backend: Backend | None = None,
) -> dict[str, Any]:
    backend = backend or get_backend()
    where: list[str] = []
    if status:
        if status not in VALID_PICKUP_STATUS:
            raise ValueError(f"invalid pickup status: {status}")
        where.append(f"status = {quote_str(status)}")
    if project_id:
        where.append(f"project_id = {quote_str(project_id)}")
    if source_agent:
        where.append(f"source_agent = {quote_str(source_agent)}")
    clause = "WHERE " + " AND ".join(where) if where else ""
    total_rows = backend.query(
        f"SELECT count() AS c FROM pickup_candidates FINAL {clause}"
    )
    rows = backend.query(
        f"SELECT {_SELECT} FROM pickup_candidates FINAL {clause} "
        f"ORDER BY created_at DESC, id LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    return {
        "total": int(total_rows[0].get("c", 0) or 0) if total_rows else 0,
        "offset": int(offset),
        "limit": int(limit),
        "items": [PickupCandidate.from_row(row).to_dict() for row in rows],
    }


def list_runs(limit: int = 30, backend: Backend | None = None) -> list[dict[str, Any]]:
    backend = backend or get_backend()
    return backend.query(
        "SELECT id, summarizer, prompt_version, since_at, agents, session_count, "
        "candidate_count, status, error, toString(created_at) AS created_at "
        f"FROM pickup_runs ORDER BY created_at DESC LIMIT {int(limit)}"
    )


def ingest_run(
    *,
    run_id: str,
    summarizer: str,
    prompt_version: str,
    since_at: str,
    sessions: Iterable[dict[str, Any]],
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Idempotently land one sync run and its proposed candidates."""
    with _ingest_lock:
        return _ingest_run(
            run_id=run_id,
            summarizer=summarizer,
            prompt_version=prompt_version,
            since_at=since_at,
            sessions=sessions,
            backend=backend,
        )


def _ingest_run(
    *,
    run_id: str,
    summarizer: str,
    prompt_version: str,
    since_at: str,
    sessions: Iterable[dict[str, Any]],
    backend: Backend | None = None,
) -> dict[str, Any]:
    backend = backend or get_backend()
    run_id = (run_id or uuid.uuid4().hex).strip()
    existing_run = backend.query(
        f"SELECT id FROM pickup_runs WHERE id = {quote_str(run_id)} LIMIT 1"
    )
    if existing_run:
        count = backend.query(
            "SELECT count() AS c FROM pickup_candidates FINAL "
            f"WHERE run_id = {quote_str(run_id)}"
        )
        return {
            "ok": True,
            "run_id": run_id,
            "skipped": "duplicate",
            "candidate_count": int(count[0].get("c", 0) or 0) if count else 0,
        }

    # "Later" means the next review cycle, not hidden forever. A new run
    # reopens snoozed proposals before adding newly observed sessions.
    snoozed = backend.query(
        f"SELECT {_SELECT} FROM pickup_candidates FINAL WHERE status = 'snoozed'"
    )
    for row in snoozed:
        _transition(
            PickupCandidate.from_row(row),
            status="pending",
            decision_reason="",
            backend=backend,
        )

    session_rows = list(sessions)
    inserted = 0
    duplicate = 0
    agents: set[str] = set()
    for session in session_rows:
        source_agent = str(session.get("agent") or "").strip()
        session_id = str(session.get("session_id") or "").strip()
        source_hash = str(session.get("source_hash") or "").strip()
        if not source_agent or not session_id or not source_hash:
            continue
        agents.add(source_agent)
        project_id = str(session.get("project_id") or "").strip()
        cwd = str(session.get("cwd") or "").strip()

        # A changed transcript makes older unresolved proposals stale.  This
        # happens before new rows are inserted and never touches accepted or
        # dismissed decisions.
        stale = backend.query(
            f"SELECT {_SELECT} FROM pickup_candidates FINAL "
            f"WHERE source_agent = {quote_str(source_agent)} "
            f"AND session_id = {quote_str(session_id)} "
            f"AND source_hash != {quote_str(source_hash)} AND status = 'pending'"
        )
        for row in stale:
            _transition(
                PickupCandidate.from_row(row),
                status="superseded",
                decision_reason="source session changed",
                backend=backend,
            )

        for proposal in list(session.get("candidates") or []):
            content = str(proposal.get("content") or "").strip()
            if not content:
                continue
            candidate_id = _candidate_id(source_agent, session_id, source_hash, content)
            if get(candidate_id, backend=backend) is not None:
                duplicate += 1
                continue
            tags = [str(tag).strip() for tag in proposal.get("tags") or [] if str(tag).strip()]
            if not tags:
                tags = ["pickup"]
            confidence = max(0.0, min(1.0, float(proposal.get("confidence", 0.0) or 0.0)))
            now = utc_now_sql()
            backend.execute(
                "INSERT INTO pickup_candidates "
                "(id, run_id, source_agent, session_id, source_hash, project_id, cwd, "
                "content, kind, tags, suggested_scope, reason, evidence, confidence, "
                "status, decision_reason, memory_ref, version, prompt_version, content_hash, "
                "created_at, updated_at) VALUES ("
                f"{quote_str(candidate_id)}, {quote_str(run_id)}, {quote_str(source_agent)}, "
                f"{quote_str(session_id)}, {quote_str(source_hash)}, {quote_str(project_id)}, "
                f"{quote_str(cwd)}, {quote_str(content)}, "
                f"{quote_str(_coerce_kind(str(proposal.get('kind') or 'free')))}, "
                f"{quote_array_str(tags)}, "
                f"{quote_str(_coerce_scope(str(proposal.get('suggested_scope') or 'personal')))}, "
                f"{quote_str(str(proposal.get('reason') or ''))}, "
                f"{quote_str(str(proposal.get('evidence') or '')[:2000])}, {confidence:.6f}, "
                f"'pending', '', '', 1, {quote_str(prompt_version)}, "
                f"{quote_str(_content_hash(content))}, {now}, {now})"
            )
            inserted += 1

    backend.execute(
        "INSERT INTO pickup_runs "
        "(id, summarizer, prompt_version, since_at, agents, session_count, "
        "candidate_count, status, error, created_at) VALUES ("
        f"{quote_str(run_id)}, {quote_str(summarizer)}, {quote_str(prompt_version)}, "
        f"{quote_str(since_at)}, {quote_array_str(sorted(agents))}, {len(session_rows)}, "
        f"{inserted}, 'completed', '', {utc_now_sql()})"
    )
    event_write(
        "pickup.run",
        message=f"prepared {inserted} memory candidates; added 0 memories automatically",
        payload={
            "run_id": run_id,
            "session_count": len(session_rows),
            "candidate_count": inserted,
            "duplicate_candidates": duplicate,
        },
        backend=backend,
    )
    return {
        "ok": True,
        "run_id": run_id,
        "session_count": len(session_rows),
        "candidate_count": inserted,
        "duplicate_candidates": duplicate,
    }


def _transition(
    candidate: PickupCandidate,
    *,
    status: str,
    decision_reason: str = "",
    memory_ref: str = "",
    content: str | None = None,
    kind: str | None = None,
    tags: Iterable[str] | None = None,
    backend: Backend,
) -> PickupCandidate:
    if status not in VALID_PICKUP_STATUS:
        raise ValueError(f"invalid pickup status: {status}")
    next_content = (content.strip() if content is not None else candidate.content)
    next_kind = _coerce_kind(kind) if kind is not None else candidate.kind
    next_tags = list(tags) if tags is not None else candidate.tags
    next_version = candidate.version + 1
    backend.execute(
        "INSERT INTO pickup_candidates "
        "(id, run_id, source_agent, session_id, source_hash, project_id, cwd, "
        "content, kind, tags, suggested_scope, reason, evidence, confidence, "
        "status, decision_reason, memory_ref, version, prompt_version, content_hash, "
        "created_at, updated_at) VALUES ("
        f"{quote_str(candidate.id)}, {quote_str(candidate.run_id)}, "
        f"{quote_str(candidate.source_agent)}, {quote_str(candidate.session_id)}, "
        f"{quote_str(candidate.source_hash)}, {quote_str(candidate.project_id)}, "
        f"{quote_str(candidate.cwd)}, {quote_str(next_content)}, {quote_str(next_kind)}, "
        f"{quote_array_str(next_tags)}, {quote_str(candidate.suggested_scope)}, "
        f"{quote_str(candidate.reason)}, {quote_str(candidate.evidence)}, "
        f"{candidate.confidence:.6f}, {quote_str(status)}, {quote_str(decision_reason)}, "
        f"{quote_str(memory_ref)}, {next_version}, {quote_str(candidate.prompt_version)}, "
        f"{quote_str(_content_hash(next_content))}, {_dt_sql(candidate.created_at)}, "
        f"{utc_now_sql()})"
    )
    updated = get(candidate.id, backend=backend)
    assert updated is not None
    return updated


def _require_pending(candidate_id: str, version: int, backend: Backend) -> PickupCandidate:
    candidate = get(candidate_id, backend=backend)
    if candidate is None:
        raise KeyError(candidate_id)
    if candidate.version != int(version) or candidate.status != "pending":
        raise CandidateVersionConflict(candidate)
    return candidate


def accept(
    candidate_id: str,
    *,
    version: int,
    scope: str,
    content: str | None = None,
    kind: str | None = None,
    tags: Iterable[str] | None = None,
    agent: str = "pickup",
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Promote one current candidate through the normal Memory API."""
    backend = backend or get_backend()
    selected_scope = _coerce_scope(scope)
    with _accept_lock:
        candidate = _require_pending(candidate_id, version, backend)
        final_content = content.strip() if content is not None else candidate.content
        final_kind = _coerce_kind(kind) if kind is not None else candidate.kind
        final_tags = list(tags) if tags is not None else candidate.tags
        if not final_tags:
            final_tags = ["pickup"]
        source_ref = f"pickup:{candidate.source_agent}:{candidate.session_id}:{candidate.id}"
        if selected_scope == "project":
            if not candidate.project_id:
                raise ValueError("project pickup requires a candidate project_id")
            from clickmem.scopes import remember_project

            result = remember_project(
                final_content,
                project_id=candidate.project_id or "global",
                tags=final_tags,
                kind=final_kind,
                privacy="private",
                source="pickup",
                source_ref=source_ref,
                agent=agent,
            )
        else:
            from clickmem.memories import add

            result = add(
                final_content,
                kind=final_kind,
                source="pickup",
                source_ref=source_ref,
                project_id=candidate.project_id,
                privacy="private",
                tags=final_tags,
                agent=agent,
                backend=backend,
            )

        if result.get("status") in {"refused", "rejected", "cas_conflict"} or not result.get("id"):
            return {"candidate": candidate.to_dict(), "memory": result, "accepted": False}
        memory_ref = f"{selected_scope}:{result['id']}"
        updated = _transition(
            candidate,
            status=f"accepted_{selected_scope}",
            decision_reason="explicit user pickup",
            memory_ref=memory_ref,
            content=final_content,
            kind=final_kind,
            tags=final_tags,
            backend=backend,
        )
        event_write(
            "pickup.accept",
            agent=agent,
            project_id=candidate.project_id,
            memory_id=str(result["id"]),
            message=f"accepted candidate into {selected_scope} memory",
            payload={"candidate_id": candidate.id, "memory_ref": memory_ref},
            backend=backend,
        )
        return {"candidate": updated.to_dict(), "memory": result, "accepted": True}


def dismiss(
    candidate_id: str,
    *,
    version: int,
    reason: str = "temporary",
    backend: Backend | None = None,
) -> dict[str, Any]:
    backend = backend or get_backend()
    with _accept_lock:
        candidate = _require_pending(candidate_id, version, backend)
        updated = _transition(
            candidate,
            status="dismissed",
            decision_reason=reason or "temporary",
            backend=backend,
        )
    return updated.to_dict()


def snooze(
    candidate_id: str,
    *,
    version: int,
    reason: str = "later",
    backend: Backend | None = None,
) -> dict[str, Any]:
    backend = backend or get_backend()
    with _accept_lock:
        candidate = _require_pending(candidate_id, version, backend)
        updated = _transition(
            candidate,
            status="snoozed",
            decision_reason=reason or "later",
            backend=backend,
        )
    return updated.to_dict()
