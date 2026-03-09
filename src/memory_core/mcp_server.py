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

from memory_core.models import RetrievalConfig

_log = logging.getLogger("clickmem.mcp")

server = Server("clickmem")
_transport = None

_HTTP_PORT = int(os.environ.get("CLICKMEM_SERVER_PORT", "9527"))
_HTTP_HOST = "127.0.0.1"


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
# Tools
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="clickmem_recall",
            description="Search memories by semantic query. Returns relevant memories ranked by score.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {"type": "integer", "description": "Max results (default: 10)", "default": 10},
                    "min_score": {"type": "number", "description": "Minimum score threshold (default: 0.0)", "default": 0.0},
                    "layer": {"type": "string", "description": "Filter by layer: episodic, semantic, or null for all", "enum": ["episodic", "semantic"]},
                    "category": {"type": "string", "description": "Filter by category"},
                    "max_content_length": {"type": "integer", "description": "Truncate content to this many chars (default: 800, 0=no limit)", "default": 800},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="clickmem_remember",
            description="Store a new memory. Use layer='semantic' for long-term facts, 'episodic' for events, 'working' for current focus.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Memory content to store"},
                    "layer": {"type": "string", "description": "Memory layer", "enum": ["working", "episodic", "semantic"], "default": "semantic"},
                    "category": {"type": "string", "description": "Category", "default": "knowledge"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags", "default": []},
                    "no_upsert": {"type": "boolean", "description": "Skip dedup, force insert", "default": False},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="clickmem_extract",
            description="Extract structured memories from conversation text using LLM analysis.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Conversation text to extract from"},
                    "session_id": {"type": "string", "description": "Session ID", "default": ""},
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="clickmem_forget",
            description="Delete a memory by ID, UUID prefix, or content search.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id_or_content": {"type": "string", "description": "Memory ID, prefix, or content description"},
                },
                "required": ["id_or_content"],
            },
        ),
        Tool(
            name="clickmem_status",
            description="Show memory statistics: counts per layer and total.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="clickmem_working",
            description="Get or set working memory (L0). Omit 'content' to read current value.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "New working memory content (omit to read)"},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    t = _get_transport()

    if name == "clickmem_recall":
        cfg = RetrievalConfig(
            top_k=arguments.get("top_k", 10),
            layer=arguments.get("layer"),
            category=arguments.get("category"),
        )
        results = await asyncio.to_thread(
            t.recall, arguments["query"], cfg=cfg,
            min_score=arguments.get("min_score", 0.0),
        )
        if not results:
            return [TextContent(type="text", text="No matching memories found.")]
        max_len = arguments.get("max_content_length", 800)
        lines = []
        for r in results:
            score = r.get("final_score", 0)
            content = r["content"]
            if max_len and len(content) > max_len:
                content = content[:max_len] + "… [truncated]"
            lines.append(f"[{r['layer']}/{r.get('category', '')}] (score={score:.2f}) {content}")
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "clickmem_remember":
        result = await asyncio.to_thread(
            t.remember,
            content=arguments["content"],
            layer=arguments.get("layer", "semantic"),
            category=arguments.get("category", "knowledge"),
            tags=arguments.get("tags", []),
            no_upsert=arguments.get("no_upsert", False),
        )
        return _json_text(result)

    if name == "clickmem_extract":
        ids = await asyncio.to_thread(
            t.extract,
            text=arguments["text"],
            session_id=arguments.get("session_id", ""),
        )
        return _json_text({"extracted": len(ids), "ids": ids})

    if name == "clickmem_forget":
        result = await asyncio.to_thread(t.forget, arguments["id_or_content"])
        return _json_text(result)

    if name == "clickmem_status":
        return _json_text(await asyncio.to_thread(t.status))

    if name == "clickmem_working":
        content = arguments.get("content")
        if content is not None:
            result = await asyncio.to_thread(t.remember, content=content, layer="working")
            return _json_text(result)
        working = await asyncio.to_thread(t.review, layer="working")
        if working:
            return [TextContent(type="text", text=str(working))]
        return [TextContent(type="text", text="No working memory set.")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@server.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="clickmem://status",
            name="Memory Status",
            description="Current memory layer counts and statistics",
            mimeType="application/json",
        ),
        Resource(
            uri="clickmem://working",
            name="Working Memory",
            description="Current L0 working memory content",
            mimeType="text/plain",
        ),
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    t = _get_transport()

    if str(uri) == "clickmem://status":
        data = await asyncio.to_thread(t.status)
        return json.dumps(data, default=str, ensure_ascii=False)

    if str(uri) == "clickmem://working":
        working = await asyncio.to_thread(t.review, layer="working")
        return str(working) if working else "(empty)"

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
