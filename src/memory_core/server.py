"""ClickMem REST API Server — FastAPI-based HTTP service for LAN memory sharing.

Start with: memory serve --host 0.0.0.0 --port 9527
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from memory_core.auth import verify_api_key
from memory_core.transport import LocalTransport

_log = logging.getLogger("clickmem.server")


class _McpInitFilter(logging.Filter):
    def filter(self, record):
        return "Received request before initialization" not in record.getMessage()

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RecallRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    layer: Optional[str] = None
    category: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None
    enhanced: bool = False


class RememberRequest(BaseModel):
    content: str
    layer: str = "semantic"
    category: str = "knowledge"
    tags: list[str] = Field(default_factory=list)
    no_upsert: bool = False


class ExtractRequest(BaseModel):
    text: str
    session_id: str = ""


class IngestRequest(BaseModel):
    text: str
    session_id: str = ""
    source: str = "cursor"
    cwd: str = ""


class MaintainRequest(BaseModel):
    dry_run: bool = False


class SqlRequest(BaseModel):
    query: str


# ---------------------------------------------------------------------------
# App lifecycle — load heavy resources once
# ---------------------------------------------------------------------------

_transport = None


def _get_transport():
    global _transport
    if _transport is None:
        from memory_core.transport import LocalTransport
        _transport = LocalTransport()
    return _transport


@asynccontextmanager
async def lifespan(application: FastAPI):
    t = _get_transport()
    if isinstance(t, LocalTransport):
        raw_counts = t._get_db().count_raw()
        if raw_counts.get("unprocessed", 0) > 0:
            _log.info("Startup: %d unprocessed raw, triggering refinement", raw_counts["unprocessed"])
            t._trigger_refinement()

        try:
            ceo_db = t._get_ceo_db()
            emb = t._get_emb()
            from memory_core.ceo_maintenance import CEOMaintenance
            deduped = await asyncio.to_thread(CEOMaintenance.dedup_principles, ceo_db, emb)
            if deduped:
                _log.info("Startup: deduped %d CEO principles", deduped)
        except Exception as exc:
            _log.debug("Startup CEO dedup skipped: %s", exc)
    yield


app = FastAPI(
    title="ClickMem",
    description="Unified memory center for AI coding agents",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

_api_key_env: str | None = None


def _get_expected_key() -> str:
    global _api_key_env
    if _api_key_env is None:
        _api_key_env = os.environ.get("CLICKMEM_API_KEY", "")
    return _api_key_env


async def auth_dep(authorization: Optional[str] = Header(None)):
    expected = _get_expected_key()
    if not expected:
        return
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not verify_api_key(token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Debug-mode guard for SQL endpoint
# ---------------------------------------------------------------------------

_debug_mode = False


def set_debug_mode(enabled: bool):
    global _debug_mode
    _debug_mode = enabled


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/health")
async def health():
    t = _get_transport()
    return await asyncio.to_thread(t.health)


@app.post("/v1/recall", dependencies=[Depends(auth_dep)])
async def recall(req: RecallRequest):
    from memory_core.models import RetrievalConfig
    t = _get_transport()
    cfg = RetrievalConfig(
        top_k=req.top_k,
        layer=req.layer,
        category=req.category,
        since=req.since,
        until=req.until,
    )
    results = await asyncio.to_thread(
        t.recall, req.query, cfg=cfg, min_score=req.min_score,
        enhanced=req.enhanced,
    )
    return {"memories": results}


@app.post("/v1/remember", dependencies=[Depends(auth_dep)])
async def remember(req: RememberRequest):
    t = _get_transport()
    return await asyncio.to_thread(
        t.remember,
        content=req.content, layer=req.layer,
        category=req.category, tags=req.tags,
        no_upsert=req.no_upsert,
    )


@app.post("/v1/extract", dependencies=[Depends(auth_dep)])
async def extract(req: ExtractRequest):
    t = _get_transport()
    ids = await asyncio.to_thread(t.extract, text=req.text, session_id=req.session_id)
    return {"ids": ids}


@app.post("/v1/ingest", dependencies=[Depends(auth_dep)])
async def ingest(req: IngestRequest):
    t = _get_transport()
    return await asyncio.to_thread(
        t.ingest, text=req.text, session_id=req.session_id,
        source=req.source, cwd=req.cwd,
    )


@app.delete("/v1/forget/{memory_id}", dependencies=[Depends(auth_dep)])
async def forget(memory_id: str):
    t = _get_transport()
    result = await asyncio.to_thread(t.forget, memory_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/v1/review", dependencies=[Depends(auth_dep)])
async def review(layer: str = "semantic", limit: int = 100):
    t = _get_transport()
    data = await asyncio.to_thread(t.review, layer=layer, limit=limit)
    if layer == "working":
        return {"layer": "working", "content": data}
    memories = []
    if isinstance(data, list):
        for m in data:
            if hasattr(m, "content"):
                memories.append({
                    "id": m.id, "layer": m.layer, "category": m.category,
                    "content": m.content, "tags": m.tags,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                })
            else:
                memories.append(m)
    return {"layer": layer, "memories": memories}


@app.get("/v1/list", dependencies=[Depends(auth_dep)])
async def list_memories(
    layer: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "created_at",
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    t = _get_transport()
    memories = await asyncio.to_thread(
        t.list_memories,
        layer=layer, category=category, limit=limit, offset=offset,
        sort_by=sort_by, since=since, until=until,
    )
    result = []
    for m in memories:
        if hasattr(m, "content"):
            result.append({
                "id": m.id, "layer": m.layer, "category": m.category,
                "content": m.content, "tags": m.tags, "entities": m.entities,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "access_count": m.access_count,
            })
        else:
            result.append(m)
    return {"memories": result, "count": len(result)}


@app.api_route("/v1/status", methods=["GET", "POST"], dependencies=[Depends(auth_dep)])
async def status():
    t = _get_transport()
    return await asyncio.to_thread(t.status)


@app.post("/v1/maintain", dependencies=[Depends(auth_dep)])
async def maintain(req: MaintainRequest):
    t = _get_transport()
    return await asyncio.to_thread(t.maintain, dry_run=req.dry_run)


@app.post("/v1/backfill", dependencies=[Depends(auth_dep)])
async def backfill_endpoint():
    """Reset processed raws with no entities so refinement can retry them."""
    t = _get_transport()
    db = t._get_db()
    reset = await asyncio.to_thread(db.reset_orphan_processed_raws)
    return {"reset": reset}


_READONLY_SQL_PREFIXES = ("SELECT", "SHOW", "DESCRIBE", "EXISTS", "EXPLAIN")


@app.post("/v1/sql", dependencies=[Depends(auth_dep)])
async def sql(req: SqlRequest):
    normalized = req.query.strip().upper().split()[0] if req.query.strip() else ""
    is_readonly = normalized in _READONLY_SQL_PREFIXES
    if not is_readonly and not _debug_mode:
        raise HTTPException(
            status_code=403,
            detail="Write SQL requires --debug mode. Read-only queries (SELECT/SHOW/DESCRIBE) are always allowed.",
        )
    t = _get_transport()
    try:
        results = await asyncio.to_thread(t.sql, req.query)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Claude Code Hooks — HTTP hook endpoint for automatic recall & capture.
#
# Claude Code fires hooks at lifecycle points (SessionStart, Stop, etc.) and
# can POST the event payload directly to an HTTP endpoint.  This eliminates
# the need for an external script — the ClickMem server handles everything.
#
# Hook registration (in ~/.claude/settings.json):
#   { "hooks": { "SessionStart": [{ "hooks": [{
#       "type": "http", "url": "http://127.0.0.1:9527/hooks/claude-code" }] }], ... }}
# ---------------------------------------------------------------------------

_cc_prompt_buffers: dict[str, str] = {}

_CC_MAX_TURN_CHARS = 8000
_CC_RECALL_TOP_K = 5
_CC_RECALL_MIN_SCORE = 0.25


@app.post("/hooks/claude-code")
async def claude_code_hook(request: Request):
    """Route Claude Code hook events to the appropriate handler.

    Handles SessionStart (recall), UserPromptSubmit (buffer prompt),
    Stop (extract memories), and SessionEnd (maintenance).
    All errors are swallowed — hooks must never block Claude Code.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({})

    event = payload.get("hook_event_name", "")
    try:
        if event == "SessionStart":
            return JSONResponse(await _cc_session_start(payload))
        elif event == "UserPromptSubmit":
            return JSONResponse(await _cc_user_prompt_submit(payload))
        elif event == "Stop":
            return JSONResponse(await _cc_stop(payload))
        elif event == "SessionEnd":
            return JSONResponse(await _cc_session_end(payload))
        elif event == "PostToolUse":
            return JSONResponse(await _cc_post_tool_use(payload))
        else:
            return JSONResponse({})
    except Exception as exc:
        _log.debug("claude-code hook %s failed: %s", event, exc)
        return JSONResponse({})


async def _cc_session_start(payload: dict) -> dict:
    """Build CEO context and inject as additionalContext.

    Also triggers session replay in the background to recover any
    turns missed by hooks in previous sessions.
    """
    from memory_core.context_engine import build_ceo_context
    from memory_core.project_detect import detect_project

    cwd = payload.get("cwd", "")
    session_id = payload.get("session_id", "")

    t = _get_transport()
    ceo_db = t._get_ceo_db()
    emb = t._get_emb()

    # Background: replay any missed turns from previous sessions
    try:
        from memory_core.session_replay import replay_missing_turns
        replay_result = await asyncio.to_thread(
            replay_missing_turns, t, cwd=cwd, current_session_id=session_id,
        )
        if replay_result.get("turns_replayed", 0) > 0:
            _log.info(
                "claude-code SessionStart: replayed %d missed turns from %d sessions",
                replay_result["turns_replayed"], replay_result["sessions_checked"],
            )
    except Exception as exc:
        _log.debug("Session replay failed: %s", exc)

    project_id = await asyncio.to_thread(
        detect_project, ceo_db, cwd=cwd, emb=emb,
    )

    context = await asyncio.to_thread(
        build_ceo_context, ceo_db, emb,
        project_id=project_id, agent_source="claude_code",
    )

    if not context or not context.strip():
        return {}

    workspace_name = os.path.basename(cwd) if cwd else ""
    _log.info("claude-code SessionStart: injected CEO context for %s (project=%s)",
              workspace_name, project_id[:8] if project_id else "none")
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }


async def _cc_user_prompt_submit(payload: dict) -> dict:
    """Buffer the user prompt so the Stop handler can build the full turn."""
    session_id = payload.get("session_id", "")
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        import json as _json
        prompt = _json.dumps(prompt, ensure_ascii=False) if prompt else ""
    if session_id and prompt:
        _cc_prompt_buffers[session_id] = prompt
    return {}


async def _cc_stop(payload: dict) -> dict:
    """Extract memories from the completed turn (buffered prompt + assistant response)."""
    session_id = payload.get("session_id", "")
    assistant_msg = payload.get("last_assistant_message", "")
    if not isinstance(assistant_msg, str):
        import json as _json
        assistant_msg = _json.dumps(assistant_msg, ensure_ascii=False) if assistant_msg else ""

    if not assistant_msg or len(assistant_msg) < 20:
        return {}

    user_prompt = _cc_prompt_buffers.pop(session_id, "")
    if user_prompt:
        turn_text = f"user: {user_prompt}\nassistant: {assistant_msg}"
    else:
        turn_text = f"assistant: {assistant_msg}"

    if len(turn_text) > _CC_MAX_TURN_CHARS:
        turn_text = turn_text[:_CC_MAX_TURN_CHARS]

    if len(turn_text) < 40:
        return {}

    cwd = payload.get("cwd", "")
    t = _get_transport()
    try:
        result = await asyncio.to_thread(
            t.ingest, text=turn_text, session_id=session_id,
            source="claude_code", cwd=cwd,
        )
        raw_id = result.get("raw_id", "")
        ep_count = len(result.get("episodes", []))
        dec_count = len(result.get("decisions", []))
        _log.info(
            "claude-code Stop: ingested raw_id=%s, %d episodes, %d decisions from %d chars",
            raw_id[:8] if raw_id else "?", ep_count, dec_count, len(turn_text),
        )
    except Exception as exc:
        _log.debug("claude-code Stop ingest failed: %s", exc)

    # Sync auto-memory files (direct parse, no LLM)
    if cwd:
        try:
            from memory_core.import_agent import sync_project_memories
            ceo_db = t._get_ceo_db()
            emb = t._get_emb()
            sync_result = await asyncio.to_thread(
                sync_project_memories, ceo_db, emb, cwd,
            )
            if sync_result.get("synced", 0) > 0:
                _log.info(
                    "claude-code Stop: synced %d auto-memory files",
                    sync_result["synced"],
                )
        except Exception as exc:
            _log.debug("claude-code Stop auto-memory sync failed: %s", exc)

    return {}


async def _cc_session_end(payload: dict) -> dict:
    """Clean up prompt buffer, session context, and run lightweight maintenance."""
    session_id = payload.get("session_id", "")
    _cc_prompt_buffers.pop(session_id, None)

    # Clean up session context
    if session_id:
        try:
            from memory_core.session_context import get_session_store
            get_session_store().remove(session_id)
        except Exception:
            pass

    # Clean up expired sessions
    try:
        from memory_core.session_context import get_session_store
        get_session_store().cleanup_expired()
    except Exception:
        pass

    t = _get_transport()
    try:
        result = await asyncio.to_thread(t.maintain)
        if result:
            parts = []
            if result.get("stale_cleaned"):
                parts.append(f"stale={result['stale_cleaned']}")
            if result.get("deleted_purged"):
                parts.append(f"purged={result['deleted_purged']}")
            if result.get("promoted"):
                parts.append(f"promoted={result['promoted']}")
            if parts:
                _log.info("claude-code SessionEnd maintenance: %s", ", ".join(parts))
    except Exception as exc:
        _log.debug("claude-code SessionEnd maintenance failed: %s", exc)

    return {}


async def _cc_post_tool_use(payload: dict) -> dict:
    """Sync a single auto-memory file after a Write/Edit tool use.

    Fast-path filter: only processes files in ~/.claude/projects/*/memory/*.md.
    """
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return {}

    file_path = tool_input.get("file_path", "") or tool_input.get("path", "")
    if not file_path:
        return {}

    # Fast filter — only Claude auto-memory files
    claude_projects = os.path.expanduser("~/.claude/projects")
    if not file_path.startswith(claude_projects) or "/memory/" not in file_path:
        return {}
    if not file_path.endswith(".md") or file_path.endswith("MEMORY.md"):
        return {}

    cwd = payload.get("cwd", "")
    if not cwd:
        return {}

    t = _get_transport()
    try:
        from memory_core.import_agent import sync_single_memory_file
        ceo_db = t._get_ceo_db()
        emb = t._get_emb()
        result = await asyncio.to_thread(
            sync_single_memory_file, ceo_db, emb, file_path, cwd,
        )
        if result.get("synced", 0) > 0:
            _log.info(
                "claude-code PostToolUse: synced %s",
                os.path.basename(file_path),
            )
    except Exception as exc:
        _log.debug("claude-code PostToolUse auto-memory sync failed: %s", exc)

    return {}


def _build_combined_app():
    """Build a combined ASGI app: FastAPI REST + MCP SSE on the same port.

    Routes:
      /sse         → MCP SSE connection (GET)
      /messages/   → MCP message posting (POST)
      /*           → FastAPI REST API
    """
    from mcp.server.sse import SseServerTransport
    from memory_core.mcp_server import server as mcp_server, set_transport

    sse_transport = SseServerTransport("/messages/")
    init_options = mcp_server.create_initialization_options()
    set_transport(_get_transport())

    rest_app = app

    async def combined(scope, receive, send):
        if scope["type"] == "lifespan":
            await rest_app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/sse":
            try:
                async with sse_transport.connect_sse(scope, receive, send) as streams:
                    await mcp_server.run(streams[0], streams[1], init_options)
            except Exception:
                pass  # Client disconnected
        elif path.startswith("/messages"):
            try:
                await sse_transport.handle_post_message(scope, receive, send)
            except Exception:
                pass  # Client disconnected
        else:
            await rest_app(scope, receive, send)

    return combined


def run_server(host: str | None = None, port: int | None = None, debug: bool = False,
               register_mdns: bool = True, mcp: bool = True):
    if host is None:
        host = os.environ.get("CLICKMEM_SERVER_HOST", "127.0.0.1")
    if port is None:
        port = int(os.environ.get("CLICKMEM_SERVER_PORT", "9527"))
    """Start the ClickMem server (blocking).

    When *mcp* is True (default), MCP SSE is served on the same port at
    ``/sse`` and ``/messages/``, so a single process handles both
    the REST API and MCP clients.
    """
    import uvicorn
    set_debug_mode(debug)

    asgi_app = _build_combined_app() if mcp else app

    mdns_cleanup = None
    if register_mdns and host in ("0.0.0.0", "::"):
        try:
            from memory_core.discovery import register_service, get_local_ip
            local_ip = get_local_ip()
            mdns_cleanup = register_service(local_ip, port)
            print(f"mDNS: registered clickmem at {local_ip}:{port}")
        except Exception as e:
            print(f"mDNS registration skipped: {e}")

    logging.getLogger("root").addFilter(_McpInitFilter())

    try:
        uvicorn.run(asgi_app, host=host, port=port, log_level="info")
    finally:
        if mdns_cleanup:
            mdns_cleanup()
