"""Personal/project memory routing for a serverless team store.

The main ClickMem process is the personal-memory actor: it owns the long-lived
backend that receives raw transcripts, events, and personal memories.  A
shared project store is different.  It is opened only for one cold operation
in a helper process, then closed immediately:

* read-only recall never takes the project lease;
* project mutations take the DAO lease with object-store CAS, inspect the
  latest state, publish the whole operation in one WAL/head CAS, and release;
* the helper process avoids opening a second chDB session beside the personal
  actor in the same process.

There is no independently deployed team service.  The helper is part of the
ClickMem package and inherits the caller's object-store credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterable
from typing import Any

from clickmem.backend import Backend, get_backend
from clickmem.config import get_config
from clickmem.embedding import embed
from clickmem.events import write as event_write

VALID_WRITE_SCOPES = {"personal", "project"}
VALID_READ_SCOPES = {"personal", "project", "all"}


class ProjectScopeUnavailable(RuntimeError):
    """Raised when project scope is requested without a configured store."""


class ProjectWorkerError(RuntimeError):
    """Raised when the local project helper cannot complete an operation."""


def normalise_write_scope(scope: str | None) -> str:
    value = (scope or "personal").strip().lower()
    if value not in VALID_WRITE_SCOPES:
        raise ValueError("write scope must be 'personal' or 'project'")
    return value


def normalise_read_scope(scope: str | None) -> str:
    value = (scope or "all").strip().lower()
    if value not in VALID_READ_SCOPES:
        raise ValueError("read scope must be 'personal', 'project', or 'all'")
    return value


def project_scope_configured() -> bool:
    return bool(get_config().project_durable_url)


def run_project_operation(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Run one project-store operation in the bundled helper process."""
    cfg = get_config()
    if not cfg.project_durable_url:
        raise ProjectScopeUnavailable(
            "project memory is not configured; set CLICKMEM_PROJECT_DURABLE_URL "
            "and CLICKMEM_PROJECT_DURABLE_ID"
        )

    request = {"op": operation, "payload": payload}
    timeout = max(5.0, float(cfg.project_write_timeout_seconds) + 10.0)
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "clickmem.project_worker"],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectWorkerError(
            f"project worker timed out after {timeout:.1f}s"
        ) from exc
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        detail = proc.stderr.strip() or f"project worker exited {proc.returncode}"
        raise ProjectWorkerError(detail)
    try:
        result = json.loads(lines[-1])
    except ValueError as exc:
        raise ProjectWorkerError(
            f"invalid project worker response: {lines[-1][:300]}"
        ) from exc
    if not isinstance(result, dict):
        raise ProjectWorkerError("project worker returned a non-object response")
    if result.get("status") == "error":
        raise ProjectWorkerError(str(result.get("message") or "project worker failed"))
    return result


def remember_project(
    content: str,
    *,
    project_id: str,
    tags: Iterable[str],
    kind: str = "free",
    privacy: str = "private",
    pinned: bool = False,
    source: str = "agent_remember",
    source_ref: str = "",
    revises_id: str = "",
    agent: str = "",
) -> dict[str, Any]:
    if not project_id or project_id.strip().lower() in {"global", "__global__"}:
        raise ValueError("scope='project' requires a non-global project_id")
    vector = embed(content.strip())
    result = run_project_operation(
        "remember",
        {
            "content": content,
            "project_id": project_id,
            "tags": list(tags or []),
            "kind": kind,
            "privacy": privacy,
            "pinned": bool(pinned),
            "source": source,
            "source_ref": source_ref,
            "revises_id": revises_id,
            "agent": agent,
            "embedding": vector,
        },
    )
    result.setdefault("scope", "project")
    return result


def recall_scoped(
    query: str,
    *,
    scope: str = "all",
    project_id: str = "",
    limit: int = 10,
    include_confidential: bool = False,
    cross_project: bool = False,
    kind: str | None = None,
    tags: Iterable[str] | None = None,
    tag_mode: str = "any",
    pool_size: int = 50,
    agent: str = "",
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Recall from the requested domains and merge their ranked results."""
    from clickmem.recall import recall

    selected = normalise_read_scope(scope)
    if not query or not query.strip():
        return {"hits": [], "timeout": False, "scope": selected}

    backend = backend or get_backend()
    vector = embed(query.strip())
    common = {
        "project_id": project_id,
        "limit": int(limit),
        "include_confidential": bool(include_confidential),
        "cross_project": bool(cross_project),
        "kind": kind,
        "tags": list(tags or []),
        "tag_mode": tag_mode,
        "pool_size": int(pool_size),
        "agent": agent,
        "query_vector": vector,
    }

    hits: list[dict[str, Any]] = []
    warnings: list[str] = []
    if selected in {"personal", "all"}:
        personal = recall(
            query,
            backend=backend,
            scope="personal",
            write_event=False,
            **common,
        )
        hits.extend(item.to_dict() for item in personal)

    if selected in {"project", "all"}:
        if project_scope_configured():
            try:
                result = run_project_operation("recall", {"query": query, **common})
                hits.extend(list(result.get("hits") or []))
                if result.get("warning"):
                    warnings.append(str(result["warning"]))
            except ProjectWorkerError as exc:
                if selected == "project":
                    raise
                warnings.append(f"project recall unavailable: {exc}")
        elif selected == "project":
            raise ProjectScopeUnavailable(
                "project memory is not configured; set CLICKMEM_PROJECT_DURABLE_URL "
                "and CLICKMEM_PROJECT_DURABLE_ID"
            )

    # A memory id is unique only inside its store; include scope in the key.
    # Project stores carry team-wide blacklist rules, while the personal actor
    # may have stricter local refusals.  Apply the personal rules once more to
    # the merged rows so shared memory cannot bypass them.
    from clickmem.blacklist import enforce_on_recall

    hits = enforce_on_recall(hits, project_id=project_id, backend=backend)
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        key = (str(hit.get("scope", "personal")), str(hit.get("id", "")))
        previous = unique.get(key)
        if previous is None or float(hit.get("score", 0.0) or 0.0) > float(
            previous.get("score", 0.0) or 0.0
        ):
            unique[key] = hit
    merged = sorted(
        unique.values(),
        key=lambda item: (
            bool(item.get("pinned")),
            float(item.get("score", 0.0) or 0.0),
        ),
        reverse=True,
    )[: int(limit)]

    event_write(
        "recall.run",
        agent=agent,
        project_id=project_id,
        message=f"scoped recall returned {len(merged)} hits",
        payload={
            "query_preview": query[:120],
            "scope": selected,
            "hit_ids": [f"{h.get('scope')}:{h.get('id')}" for h in merged],
            "tags": list(tags or []),
            "tag_mode": tag_mode,
        },
        backend=backend,
    )
    out: dict[str, Any] = {"hits": merged, "timeout": False, "scope": selected}
    if warnings:
        out["warning"] = "; ".join(warnings)
    return out


def recall_trace_scoped(
    query: str,
    *,
    scope: str = "all",
    backend: Backend | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Recall trace across both stores without writing audit rows to project."""
    from clickmem.recall import recall_trace

    selected = normalise_read_scope(scope)
    if not query or not query.strip():
        return {
            "query": query,
            "filters": {"scope": selected},
            "hits": [],
            "candidates": [],
        }
    backend = backend or get_backend()
    vector = embed(query.strip())
    common = {**kwargs, "query_vector": vector}
    traces: list[dict[str, Any]] = []
    if selected in {"personal", "all"}:
        traces.append(recall_trace(query, backend=backend, scope="personal", **common))
    if selected in {"project", "all"}:
        if project_scope_configured():
            try:
                traces.append(
                    run_project_operation("recall_trace", {"query": query, **common})
                )
            except ProjectWorkerError:
                if selected == "project":
                    raise
        elif selected == "project":
            raise ProjectScopeUnavailable(
                "project memory is not configured; set CLICKMEM_PROJECT_DURABLE_URL "
                "and CLICKMEM_PROJECT_DURABLE_ID"
            )
    hits = [item for trace in traces for item in list(trace.get("hits") or [])]
    hits.sort(
        key=lambda item: (
            bool(item.get("pinned")),
            float(item.get("score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    limit = int(kwargs.get("limit", 10))
    candidates = [
        item for trace in traces for item in list(trace.get("candidates") or [])
    ]
    return {
        "query": query,
        "filters": {**kwargs, "scope": selected},
        "hits": hits[:limit],
        "candidates": candidates,
    }
