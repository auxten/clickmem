"""ClickMem MCP Server — Model Context Protocol interface for Claude Code / Cursor.

Supports two transport modes:
- stdio: for same-machine Claude Code / Cursor (best latency)
- sse: for LAN remote access (HTTP Server-Sent Events)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp.server import Server
from mcp.types import (
    TextContent,
    Tool,
    Resource,
)

from memory_core.models import RetrievalConfig
from memory_core.transport import LocalTransport

server = Server("clickmem")
_transport: LocalTransport | None = None


def _get_transport() -> LocalTransport:
    global _transport
    if _transport is None:
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
        results = t.recall(
            arguments["query"], cfg=cfg,
            min_score=arguments.get("min_score", 0.0),
        )
        if not results:
            return [TextContent(type="text", text="No matching memories found.")]
        lines = []
        for r in results:
            score = r.get("final_score", 0)
            lines.append(f"[{r['layer']}/{r.get('category', '')}] (score={score:.2f}) {r['content']}")
        return [TextContent(type="text", text="\n".join(lines))]

    if name == "clickmem_remember":
        result = t.remember(
            content=arguments["content"],
            layer=arguments.get("layer", "semantic"),
            category=arguments.get("category", "knowledge"),
            tags=arguments.get("tags", []),
            no_upsert=arguments.get("no_upsert", False),
        )
        return _json_text(result)

    if name == "clickmem_extract":
        ids = t.extract(
            text=arguments["text"],
            session_id=arguments.get("session_id", ""),
        )
        return _json_text({"extracted": len(ids), "ids": ids})

    if name == "clickmem_forget":
        result = t.forget(arguments["id_or_content"])
        return _json_text(result)

    if name == "clickmem_status":
        return _json_text(t.status())

    if name == "clickmem_working":
        content = arguments.get("content")
        if content is not None:
            result = t.remember(content=content, layer="working")
            return _json_text(result)
        working = t.review(layer="working")
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
        return json.dumps(t.status(), default=str, ensure_ascii=False)

    if str(uri) == "clickmem://working":
        working = t.review(layer="working")
        return str(working) if working else "(empty)"

    raise ValueError(f"Unknown resource: {uri}")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


async def run_stdio():
    """Run MCP server over stdio (for local Claude Code / Cursor)."""
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def run_sse(host: str = "0.0.0.0", port: int = 9528):
    """Run MCP server over SSE (for LAN remote Cursor / Claude Code)."""
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from mcp.server.sse import SseServerTransport

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0], streams[1],
                server.create_initialization_options(),
            )

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
    )

    import uvicorn
    config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
    srv = uvicorn.Server(config)
    await srv.serve()


def main_stdio():
    """Synchronous entry point for stdio mode."""
    asyncio.run(run_stdio())


def main_sse(host: str = "0.0.0.0", port: int = 9528):
    """Synchronous entry point for SSE mode."""
    asyncio.run(run_sse(host=host, port=port))
