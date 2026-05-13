"""FastAPI app: REST + dashboard + MCP SSE mount.

Design rules enforced here:

* The server runs zero LLM. Only the embedding model is allowed.
* Every blocking call (DB, embedding) is wrapped in :func:`asyncio.to_thread`.
* Every successful mutation appends one row to the ``events`` table.
* Loopback binds are open; non-loopback binds require ``CLICKMEM_API_KEY`` as
  a bearer token.
* The dashboard is served from ``src/clickmem/dashboard/dist/`` when present;
  otherwise the ``/dashboard`` mount returns a friendly 503 with build hints.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from clickmem import __version__
from clickmem import adapters as adapters_mod
from clickmem import agents as agents_mod
from clickmem import blacklist as blacklist_mod
from clickmem import conflicts as conflicts_mod
from clickmem import events as events_mod
from clickmem import history as history_mod
from clickmem import import_docs as import_docs_mod
from clickmem import memories as memories_mod
from clickmem import projects as projects_mod
from clickmem import raw as raw_mod
from clickmem import recall as recall_mod
from clickmem import stats as stats_mod
from clickmem.backend import get_backend
from clickmem.config import get_config


_log = logging.getLogger(__name__)


# ---------- Pydantic request models ---------------------------------------


class MemoryCreate(BaseModel):
    content: str
    kind: str = "free"
    source: str = "agent_remember"
    source_ref: str = ""
    project_id: str = ""
    privacy: str = "private"
    tags: List[str] = Field(default_factory=list)
    pinned: bool = False
    revises_id: str = ""
    agent: str = ""


class MemoryPatch(BaseModel):
    content: Optional[str] = None
    kind: Optional[str] = None
    privacy: Optional[str] = None
    project_id: Optional[str] = None
    tags: Optional[List[str]] = None
    pinned: Optional[bool] = None
    revises_id: Optional[str] = None
    agent: str = ""


class MemoryBulkRequest(BaseModel):
    ids: List[str]
    op: str
    payload: dict = Field(default_factory=dict)
    agent: str = ""


class RecallRequest(BaseModel):
    query: str
    project_id: str = ""
    limit: int = 10
    include_confidential: bool = False
    cross_project: bool = False
    kind: Optional[str] = None
    agent: str = ""


class BlacklistCreate(BaseModel):
    pattern: str
    scope: str = "global"
    reason: str = ""


class ConflictResolveRequest(BaseModel):
    op: str
    peer_id: str = ""


class ProjectLinkRequest(BaseModel):
    a: str
    b: str
    reason: str = ""


class RawCreate(BaseModel):
    text: str
    session_id: str
    agent: str = ""
    project_id: str = ""
    role: str = ""
    meta: dict = Field(default_factory=dict)


class EventCreate(BaseModel):
    kind: str
    agent: str = ""
    project_id: str = ""
    memory_id: str = ""
    message: str = ""
    payload: dict = Field(default_factory=dict)


# ---------- App factory ---------------------------------------------------


def _auth_dependency():
    cfg = get_config()
    api_key = cfg.api_key

    async def _check(request: Request):
        if not api_key:
            return
        host = (request.client.host if request.client else "") or ""
        if host in ("127.0.0.1", "localhost", "::1"):
            return
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = header.split(" ", 1)[1].strip()
        if token != api_key:
            raise HTTPException(status_code=403, detail="invalid bearer token")

    return _check


def create_app() -> FastAPI:
    cfg = get_config(refresh=True)

    # Tell the local_or_remote shim that this process is the server itself.
    # Inside the server we always go through the in-process backend directly;
    # the shim must not auto-probe and HTTP-call us back via 127.0.0.1.
    from clickmem import local_or_remote as _local_or_remote

    _local_or_remote.mark_in_server_process()

    app = FastAPI(title="ClickMem", version=__version__, default_response_class=JSONResponse)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    auth = _auth_dependency()

    # ---------- Health ----------------------------------------------------

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        try:
            backend = await asyncio.to_thread(get_backend)
            await asyncio.to_thread(backend.query, "SELECT 1 AS ok")
            ok = True
        except Exception as e:
            _log.warning("health check db failed: %s", e)
            ok = False
        return {
            "ok": ok,
            "version": __version__,
            "backend": cfg.backend,
            "embedding_model": cfg.embedding_model,
            "embedding_dim": cfg.embedding_dim,
        }

    # ---------- Stats -----------------------------------------------------

    @app.get("/v1/stats/overview", dependencies=[Depends(auth)])
    async def stats_overview():
        return await asyncio.to_thread(stats_mod.overview)

    @app.get("/v1/stats/projects", dependencies=[Depends(auth)])
    async def stats_projects():
        return await asyncio.to_thread(stats_mod.by_projects)

    @app.get("/v1/stats/kinds", dependencies=[Depends(auth)])
    async def stats_kinds():
        return await asyncio.to_thread(stats_mod.by_kinds)

    @app.get("/v1/stats/privacy_mix", dependencies=[Depends(auth)])
    async def stats_privacy_mix():
        return await asyncio.to_thread(stats_mod.privacy_mix)

    # ---------- Events ----------------------------------------------------

    @app.get("/v1/events", dependencies=[Depends(auth)])
    async def events_list(
        since: Optional[str] = None,
        kind: Optional[str] = None,
        agent: Optional[str] = None,
        limit: int = Query(200, ge=1, le=2000),
    ):
        return await asyncio.to_thread(events_mod.list_events, since=since, kind=kind, agent=agent, limit=limit)

    @app.post("/v1/events", dependencies=[Depends(auth)])
    async def events_create(body: EventCreate):
        await asyncio.to_thread(
            events_mod.write,
            body.kind,
            agent=body.agent,
            project_id=body.project_id,
            memory_id=body.memory_id,
            message=body.message,
            payload=body.payload,
        )
        return {"ok": True}

    # ---------- Memories: list / get / mutate -----------------------------

    @app.get("/v1/memories", dependencies=[Depends(auth)])
    async def memories_list(
        project_id: Optional[str] = None,
        privacy: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        pinned: Optional[bool] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=500),
    ):
        return await asyncio.to_thread(
            memories_mod.list_paginated,
            project_id=project_id,
            privacy=privacy,
            kind=kind,
            status=status,
            pinned=pinned,
            source=source,
            search=search,
            offset=offset,
            limit=limit,
        )

    @app.post("/v1/memories", dependencies=[Depends(auth)])
    async def memories_create(body: MemoryCreate):
        return await asyncio.to_thread(
            memories_mod.add,
            body.content,
            kind=body.kind,
            source=body.source,
            source_ref=body.source_ref,
            project_id=body.project_id,
            privacy=body.privacy,
            tags=body.tags,
            pinned=body.pinned,
            revises_id=body.revises_id,
            agent=body.agent,
        )

    @app.post("/v1/memories/bulk", dependencies=[Depends(auth)])
    async def memories_bulk(body: MemoryBulkRequest):
        return await asyncio.to_thread(
            memories_mod.bulk,
            body.ids,
            body.op,
            payload=body.payload,
            agent=body.agent,
        )

    @app.get("/v1/memories/{memory_id}", dependencies=[Depends(auth)])
    async def memories_get(memory_id: str):
        item = await asyncio.to_thread(memories_mod.get, memory_id)
        if item is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return item.to_dict()

    @app.patch("/v1/memories/{memory_id}", dependencies=[Depends(auth)])
    async def memories_patch(memory_id: str, body: MemoryPatch):
        return await asyncio.to_thread(
            memories_mod.edit,
            memory_id,
            content=body.content,
            kind=body.kind,
            privacy=body.privacy,
            project_id=body.project_id,
            tags=body.tags,
            pinned=body.pinned,
            revises_id=body.revises_id,
            agent=body.agent,
        )

    @app.delete("/v1/memories/{memory_id}", dependencies=[Depends(auth)])
    async def memories_delete(memory_id: str, reason: str = "", agent: str = ""):
        return await asyncio.to_thread(memories_mod.forget, memory_id, reason=reason, agent=agent)

    @app.get("/v1/memories/{memory_id}/history", dependencies=[Depends(auth)])
    async def memories_history(memory_id: str):
        return await asyncio.to_thread(history_mod.history_with_diffs, memory_id)

    @app.get("/v1/memories/{memory_id}/neighbors", dependencies=[Depends(auth)])
    async def memories_neighbors(memory_id: str, limit: int = Query(10, ge=1, le=50)):
        return await asyncio.to_thread(memories_mod.neighbors, memory_id, limit=limit)

    # ---------- Conflicts -------------------------------------------------

    @app.get("/v1/conflicts", dependencies=[Depends(auth)])
    async def conflicts_list(project_id: Optional[str] = None, limit: int = Query(200, ge=1, le=1000)):
        return await asyncio.to_thread(conflicts_mod.list_conflicts, project_id=project_id, limit=limit)

    @app.post("/v1/conflicts/{memory_id}/resolve", dependencies=[Depends(auth)])
    async def conflicts_resolve(memory_id: str, body: ConflictResolveRequest):
        try:
            return await asyncio.to_thread(
                conflicts_mod.resolve, memory_id, body.op, peer_id=body.peer_id
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # ---------- Recall ----------------------------------------------------

    @app.post("/v1/recall", dependencies=[Depends(auth)])
    async def recall_endpoint(body: RecallRequest):
        hits = await asyncio.to_thread(
            recall_mod.recall,
            body.query,
            project_id=body.project_id,
            limit=body.limit,
            include_confidential=body.include_confidential,
            cross_project=body.cross_project,
            kind=body.kind,
            agent=body.agent,
        )
        return {"hits": [h.to_dict() for h in hits]}

    @app.post("/v1/recall/trace", dependencies=[Depends(auth)])
    async def recall_trace_endpoint(body: RecallRequest):
        return await asyncio.to_thread(
            recall_mod.recall_trace,
            body.query,
            project_id=body.project_id,
            limit=body.limit,
            include_confidential=body.include_confidential,
            cross_project=body.cross_project,
            kind=body.kind,
            agent=body.agent,
        )

    @app.post("/v1/recall_trace", dependencies=[Depends(auth)])
    async def recall_trace_alias(body: RecallRequest):
        # Plan lists both /v1/recall_trace and /v1/recall/trace; same handler.
        return await recall_trace_endpoint(body)  # type: ignore[misc]

    # ---------- Blacklist -------------------------------------------------

    @app.get("/v1/blacklist", dependencies=[Depends(auth)])
    async def blacklist_list():
        items = await asyncio.to_thread(blacklist_mod.list_all)
        return [b.to_dict() for b in items]

    @app.post("/v1/blacklist", dependencies=[Depends(auth)])
    async def blacklist_create(body: BlacklistCreate):
        item = await asyncio.to_thread(
            blacklist_mod.add, body.pattern, scope=body.scope, reason=body.reason
        )
        await asyncio.to_thread(
            events_mod.write,
            "blacklist.add",
            message=f"added {body.pattern!r}",
            payload={"id": item.id, "scope": body.scope},
        )
        return item.to_dict()

    @app.delete("/v1/blacklist/{blacklist_id}", dependencies=[Depends(auth)])
    async def blacklist_delete(blacklist_id: str):
        await asyncio.to_thread(blacklist_mod.remove, blacklist_id)
        await asyncio.to_thread(
            events_mod.write,
            "blacklist.remove",
            message=f"removed {blacklist_id}",
            payload={"id": blacklist_id},
        )
        return {"ok": True, "id": blacklist_id}

    # ---------- Projects --------------------------------------------------

    @app.get("/v1/projects", dependencies=[Depends(auth)])
    async def projects_list():
        items = await asyncio.to_thread(projects_mod.list_all)
        return [p.to_dict() for p in items]

    @app.post("/v1/projects/link", dependencies=[Depends(auth)])
    async def projects_link(body: ProjectLinkRequest):
        a, b = await asyncio.to_thread(projects_mod.link, body.a, body.b, body.reason)
        await asyncio.to_thread(
            events_mod.write,
            "project.link",
            message=f"linked {body.a} <-> {body.b}",
            payload={"a": body.a, "b": body.b, "reason": body.reason},
        )
        return {"a": a.to_dict(), "b": b.to_dict()}

    # ---------- Raw landing -----------------------------------------------

    @app.post("/v1/raw", dependencies=[Depends(auth)])
    async def raw_append(body: RawCreate):
        return await asyncio.to_thread(
            raw_mod.append,
            body.text,
            session_id=body.session_id,
            agent=body.agent,
            project_id=body.project_id,
            role=body.role,
            meta=body.meta,
        )

    @app.get("/v1/get_raw", dependencies=[Depends(auth)])
    async def raw_get(
        session_id: Optional[str] = None,
        agent: Optional[str] = None,
        last: int = Query(50, ge=1, le=2000),
    ):
        return await asyncio.to_thread(
            raw_mod.get_raw, session_id=session_id, last=last, agent=agent
        )

    @app.get("/v1/get-raw", dependencies=[Depends(auth)])
    async def raw_get_dashed(
        session_id: Optional[str] = None,
        agent: Optional[str] = None,
        last: int = Query(50, ge=1, le=2000),
    ):
        # Dashboard uses /v1/get-raw; the plan documents both spellings.
        return await raw_get(session_id=session_id, agent=agent, last=last)  # type: ignore[misc]

    # ---------- Agents ----------------------------------------------------

    @app.get("/v1/agents", dependencies=[Depends(auth)])
    async def agents_list():
        return await asyncio.to_thread(agents_mod.list_agents)

    @app.get("/v1/agents/_all/activity", dependencies=[Depends(auth)])
    async def agents_activity_all(hours: int = Query(24, ge=1, le=24 * 14)):
        """Aggregate activity across all agents — one bucket per hour over ``hours``.

        Used by the dashboard's Agents page as an acceleration over the
        client-side bin of ``/v1/events``. The client falls back to that path
        when this endpoint returns 404 (older servers).
        """
        return await asyncio.to_thread(
            events_mod.activity_counts,
            hours=hours,
            bucket_minutes=60,
        )

    @app.get("/v1/agents/{name}/activity", dependencies=[Depends(auth)])
    async def agents_activity(name: str, hours: int = Query(24, ge=1, le=24 * 14)):
        return await asyncio.to_thread(agents_mod.activity, name, hours=hours)

    @app.post("/v1/agents/{name}/install", dependencies=[Depends(auth)])
    async def agents_install(name: str):
        result = await asyncio.to_thread(agents_mod.install, name, cfg.server_url())
        await asyncio.to_thread(
            events_mod.write, "agent.install", agent=name, message=result.get("message", "")
        )
        return result

    @app.post("/v1/agents/{name}/uninstall", dependencies=[Depends(auth)])
    async def agents_uninstall(name: str):
        result = await asyncio.to_thread(agents_mod.uninstall, name)
        await asyncio.to_thread(
            events_mod.write, "agent.uninstall", agent=name, message=result.get("message", "")
        )
        return result

    @app.post("/v1/agents/{name}/test", dependencies=[Depends(auth)])
    async def agents_test(name: str):
        return await asyncio.to_thread(agents_mod.test, name)

    # ---------- Adapter-scoped doc imports -------------------------------

    @app.post("/v1/imports/{name}/run", dependencies=[Depends(auth)])
    async def imports_run(name: str):
        """Walk an adapter's ``iter_doc_paths()`` and ingest each file."""
        adapter = adapters_mod.get(name)
        if adapter is None:
            raise HTTPException(status_code=404, detail=f"unknown adapter: {name}")
        result = await asyncio.to_thread(import_docs_mod.run_for_adapter, adapter)
        return {
            "started": True,
            "name": name,
            "files_scanned": int(result.get("files_scanned", 0) or 0),
            "accepted": int(result.get("accepted", 0) or 0),
            "skipped": int(result.get("skipped", 0) or 0),
            "results": result.get("results", []),
        }

    # ---------- Dashboard mount ------------------------------------------

    _mount_dashboard(app)

    # ---------- MCP SSE mount --------------------------------------------

    try:
        from clickmem.mcp_server import build_sse_app

        sse_app = build_sse_app()
        app.mount("/sse", sse_app)
    except Exception as e:  # pragma: no cover - mount best-effort during dev
        _log.warning("MCP SSE mount failed: %s", e)

        @app.get("/sse")
        async def _sse_unavailable():
            return JSONResponse(
                {"ok": False, "error": "MCP SSE unavailable", "detail": str(e)},
                status_code=503,
            )

    return app


def _mount_dashboard(app: FastAPI) -> None:
    """Mount the SPA at ``/dashboard`` if ``dashboard/dist/`` exists, else 503.

    The SPA fallback route forwards any request whose path doesn't match a
    file on disk back to ``index.html`` so that hard-refresh on client-side
    routes like ``/dashboard/memories`` works.
    """
    from fastapi.responses import FileResponse

    here = Path(__file__).resolve().parent
    dist = here / "dashboard" / "dist"
    if dist.is_dir() and (dist / "index.html").is_file():
        index_file = dist / "index.html"

        @app.get("/dashboard", include_in_schema=False)
        async def _dashboard_root():
            return FileResponse(index_file)

        @app.get("/dashboard/{full_path:path}", include_in_schema=False)
        async def _dashboard_spa(full_path: str = ""):
            if not full_path:
                return FileResponse(index_file)
            try:
                candidate = (dist / full_path).resolve()
                candidate.relative_to(dist)
            except (ValueError, OSError):
                return FileResponse(index_file)
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)

        return

    @app.get("/dashboard")
    @app.get("/dashboard/")
    @app.get("/dashboard/{path:path}")
    async def _dashboard_missing(path: str = "") -> JSONResponse:
        return JSONResponse(
            {
                "ok": False,
                "error": "dashboard build missing",
                "hint": "run `make dashboard` to build the SPA (Phase 7).",
                "expected_path": str(dist),
            },
            status_code=503,
        )


# ---------- Module-level singleton for `uvicorn clickmem.server:app` ------


app = create_app()


def main() -> None:  # pragma: no cover - exercised by CLI `clickmem serve`
    """Run the FastAPI app with uvicorn using env-driven host/port."""
    import uvicorn

    cfg = get_config(refresh=True)
    log_level = os.environ.get("CLICKMEM_LOG_LEVEL", cfg.log_level).lower()
    uvicorn.run(
        "clickmem.server:app",
        host=cfg.server_host,
        port=cfg.server_port,
        log_level=log_level,
        reload=False,
    )


__all__ = ["create_app", "app", "main"]
