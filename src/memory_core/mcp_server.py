"""ClickMem MCP Server — Model Context Protocol interface for Claude Code / Cursor.

Supports two transport modes:
- stdio: for same-machine Claude Code / Cursor (best latency).
  When running in stdio mode, an HTTP API server is also started on port 9527
  so that CLI commands and OpenClaw plugins can share the same database.
- sse: integrated into the REST server via ``memory serve`` (single port)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.types import (
    TextContent,
    Tool,
    Resource,
)

_log = logging.getLogger("clickmem.mcp")

server = Server("clickmem")
_transport = None

_HTTP_PORT = int(os.environ.get("CLICKMEM_SERVER_PORT", "9527"))
_HTTP_HOST = os.environ.get("CLICKMEM_SERVER_HOST", "127.0.0.1")


def set_transport(t):
    """Inject a shared transport (used by the combined REST+MCP server)."""
    global _transport
    _transport = t


def _get_transport():
    global _transport
    if _transport is None:
        from memory_core.transport import LocalTransport
        _transport = LocalTransport()
    return _transport


def _json_text(data: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str, ensure_ascii=False))]


# ---------------------------------------------------------------------------
# Tools — CEO Brain capabilities
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ceo_brief",
            description="Get a detailed briefing on a project: principles, decisions, recent activity, and optionally search for specific topics.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project ID (empty for all)"},
                    "query": {"type": "string", "description": "Optional search query within the project"},
                    "session_id": {"type": "string", "description": "Session ID for topic tracking"},
                    "task_context": {"type": "string", "description": "Current task context for scope filtering"},
                },
            },
        ),
        Tool(
            name="ceo_decide",
            description="Get decision support with historical context. Finds related past decisions and principles, optionally provides LLM recommendation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The decision question"},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "Available options"},
                    "context": {"type": "string", "description": "Additional context"},
                    "project_id": {"type": "string", "description": "Project ID"},
                    "session_id": {"type": "string", "description": "Session ID for topic tracking"},
                    "task_context": {"type": "string", "description": "Current task context for scope filtering"},
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="ceo_remember",
            description="Store a structured CEO memory (decision, principle, or episode) with proper categorization.",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["decision", "principle", "episode"],
                             "description": "Entity type to store"},
                    "content": {"type": "object", "description": "Entity-specific fields"},
                    "project_id": {"type": "string", "description": "Project ID"},
                    "session_id": {"type": "string", "description": "Session ID for topic tracking"},
                },
                "required": ["type", "content"],
            },
        ),
        Tool(
            name="ceo_review",
            description="Check a proposed plan against CEO principles and past decisions for consistency.",
            inputSchema={
                "type": "object",
                "properties": {
                    "proposed_plan": {"type": "string", "description": "The plan to review"},
                    "project_id": {"type": "string", "description": "Project ID"},
                    "session_id": {"type": "string", "description": "Session ID for topic tracking"},
                    "task_context": {"type": "string", "description": "Current task context for scope filtering"},
                },
                "required": ["proposed_plan"],
            },
        ),
        Tool(
            name="ceo_retro",
            description="Run a retrospective for a project: summarise decisions, identify validated/invalidated ones, suggest new principles.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Project ID"},
                    "time_range": {"type": "string", "description": "Time range (e.g. 'last 30 days')"},
                    "session_id": {"type": "string", "description": "Session ID for topic tracking"},
                },
                "required": ["project_id"],
            },
        ),
        Tool(
            name="ceo_portfolio",
            description="Get overview of all active projects with recent activity summary and entity counts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Session ID for topic tracking"},
                },
            },
        ),
        Tool(
            name="ceo_update_outcome",
            description="Update a decision's outcome status (validated, invalidated, or unknown).",
            inputSchema={
                "type": "object",
                "properties": {
                    "decision_id": {"type": "string", "description": "Decision ID to update"},
                    "outcome_status": {"type": "string", "enum": ["validated", "invalidated", "unknown"],
                                       "description": "New outcome status"},
                    "outcome": {"type": "string", "description": "Optional description of the outcome"},
                },
                "required": ["decision_id", "outcome_status"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    t = _get_transport()
    ceo_db = t._get_ceo_db()
    emb = t._get_emb()

    from memory_core.llm import get_llm_complete
    llm = get_llm_complete()

    session_id = arguments.get("session_id", "")
    task_context = arguments.get("task_context", "")

    if name == "ceo_brief":
        from memory_core.ceo_skills import ceo_brief
        result = await asyncio.to_thread(
            ceo_brief, ceo_db, emb,
            project_id=arguments.get("project_id", ""),
            query=arguments.get("query", ""),
            session_id=session_id,
            task_context=task_context,
        )
        return _json_text(result)

    if name == "ceo_decide":
        from memory_core.ceo_skills import ceo_decide
        result = await asyncio.to_thread(
            ceo_decide, ceo_db, emb, llm,
            question=arguments["question"],
            options=arguments.get("options"),
            context=arguments.get("context", ""),
            project_id=arguments.get("project_id", ""),
            session_id=session_id,
            task_context=task_context,
        )
        return _json_text(result)

    if name == "ceo_remember":
        from memory_core.ceo_skills import ceo_remember
        result = await asyncio.to_thread(
            ceo_remember, ceo_db, emb,
            entity_type=arguments["type"],
            content=arguments["content"],
            project_id=arguments.get("project_id", ""),
            session_id=session_id,
        )
        return _json_text(result)

    if name == "ceo_review":
        from memory_core.ceo_skills import ceo_review
        result = await asyncio.to_thread(
            ceo_review, ceo_db, emb, llm,
            proposed_plan=arguments["proposed_plan"],
            project_id=arguments.get("project_id", ""),
            session_id=session_id,
            task_context=task_context,
        )
        return _json_text(result)

    if name == "ceo_retro":
        from memory_core.ceo_skills import ceo_retro
        result = await asyncio.to_thread(
            ceo_retro, ceo_db, emb, llm,
            project_id=arguments["project_id"],
            time_range=arguments.get("time_range", ""),
            session_id=session_id,
        )
        return _json_text(result)

    if name == "ceo_portfolio":
        from memory_core.ceo_skills import ceo_portfolio
        result = await asyncio.to_thread(
            ceo_portfolio, ceo_db, emb,
            session_id=session_id,
        )
        return _json_text(result)

    if name == "ceo_update_outcome":
        from memory_core.ceo_skills import ceo_update_outcome
        result = await asyncio.to_thread(
            ceo_update_outcome, ceo_db,
            decision_id=arguments["decision_id"],
            outcome_status=arguments["outcome_status"],
            outcome=arguments.get("outcome", ""),
        )
        return _json_text(result)

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="clickmem://status",
            name="CEO Brain Status",
            description="Current entity counts and statistics",
            mimeType="application/json",
        ),
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    t = _get_transport()

    if str(uri) == "clickmem://status":
        data = await asyncio.to_thread(t.status)
        return json.dumps(data, default=str, ensure_ascii=False)

    raise ValueError(f"Unknown resource: {uri}")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def _start_http_background(transport):
    """Start the HTTP API server as a background asyncio task.

    Returns ``(uvicorn_server, task)`` on success, ``(None, None)`` if
    the port is busy or dependencies are missing.
    """
    try:
        import uvicorn
        import memory_core.server as srv_mod
    except ImportError:
        _log.warning("fastapi/uvicorn not installed; HTTP API disabled")
        return None, None

    srv_mod._transport = transport
    config = uvicorn.Config(
        srv_mod.app,
        host=_HTTP_HOST,
        port=_HTTP_PORT,
        log_level="warning",
    )
    http = uvicorn.Server(config)

    async def _serve():
        try:
            await http.serve()
        except OSError as exc:
            _log.info("HTTP server failed to start (port busy?): %s", exc)
        except Exception as exc:
            _log.warning("HTTP server error: %s", exc)

    task = asyncio.create_task(_serve())
    # Give uvicorn a moment to bind the socket so callers know it's up.
    await asyncio.sleep(0.1)
    return http, task


async def run_stdio():
    """Run MCP server over stdio + HTTP API on the same port.

    1. Try to open chDB directly (LocalTransport) and start the HTTP API
       so that CLI / OpenClaw plugin can reach the same database.
    2. If chDB is locked by another process, fall back to RemoteTransport
       and relay through the existing HTTP API server.
    """
    http_server = None
    http_task = None

    try:
        from memory_core.transport import LocalTransport
        transport = LocalTransport()
        transport._get_db()
        set_transport(transport)
        http_server, http_task = await _start_http_background(transport)
        if http_server:
            print(
                f"[clickmem] HTTP API on {_HTTP_HOST}:{_HTTP_PORT}",
                file=sys.stderr,
            )
    except Exception:
        _log.info("chDB locked, connecting to existing server at port %d", _HTTP_PORT)
        try:
            from memory_core.transport import RemoteTransport
            url = f"http://{_HTTP_HOST}:{_HTTP_PORT}"
            transport = RemoteTransport(url)
            transport.health()
            set_transport(transport)
            print(f"[clickmem] connected to existing server at {url}", file=sys.stderr)
        except Exception:
            print(
                "[clickmem] FATAL: cannot open chDB (locked) and no server at "
                f"{_HTTP_HOST}:{_HTTP_PORT}. Kill stale clickmem processes.",
                file=sys.stderr,
            )
            sys.exit(1)

    try:
        from mcp.server.stdio import stdio_server
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
    finally:
        if http_server:
            http_server.should_exit = True
            if http_task:
                try:
                    await asyncio.wait_for(http_task, timeout=3.0)
                except (asyncio.TimeoutError, Exception):
                    pass


def main_stdio():
    """Synchronous entry point for stdio mode (``clickmem-mcp`` console script)."""
    asyncio.run(run_stdio())
