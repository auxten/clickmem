"""Minimal MCP stdio client used by T1.5.

We piggy-back on the `mcp` package's own stdio client (already a dependency
of ClickMem) — hand-rolling JSON-RPC content-length framing would only
duplicate code with no upside. We still record per-tool latency so
SURPRISE thresholds work.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import os.path


@dataclass
class ToolCallResult:
    name: str
    duration_s: float
    ok: bool
    payload: Any = None
    error: Optional[str] = None


@dataclass
class McpDriveResult:
    tools_seen: list[str] = field(default_factory=list)
    calls: list[ToolCallResult] = field(default_factory=list)
    fatal: Optional[str] = None
    duration_s: float = 0.0


def drive_mcp_stdio(
    command: str,
    *,
    args: Optional[list[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
    timeout: float = 60.0,
    tool_calls: Optional[list[tuple[str, dict[str, Any]]]] = None,
) -> McpDriveResult:
    """Spawn ``command`` over stdio, list tools, then call each given tool.

    Returns a structured result so the audit can compare ``tools_seen``
    against the expected ``clickmem_*`` set and inspect per-call latency.
    """
    if tool_calls is None:
        tool_calls = []
    full_env = dict(os.environ)
    if env:
        full_env.update(env)

    result = McpDriveResult()

    async def _run() -> None:
        t_start = time.time()
        params = StdioServerParameters(
            command=command,
            args=args or [],
            env=full_env,
            cwd=str(cwd) if cwd else None,
        )
        try:
            # Suppress the child's stderr noise (sentence_transformers + MCP
            # logging) so the audit's parent stderr stays readable.
            errlog = open(os.devnull, "w", encoding="utf-8")
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(session.initialize(), timeout=timeout)
                    tools_resp = await asyncio.wait_for(session.list_tools(), timeout=timeout)
                    result.tools_seen = sorted(t.name for t in tools_resp.tools)
                    for name, args_dict in tool_calls:
                        tc_start = time.time()
                        try:
                            call_resp = await asyncio.wait_for(
                                session.call_tool(name, args_dict), timeout=timeout
                            )
                            payload: Any
                            if call_resp.isError:
                                ok = False
                                payload = [
                                    getattr(c, "text", getattr(c, "data", str(c)))
                                    for c in (call_resp.content or [])
                                ]
                            else:
                                ok = True
                                if getattr(call_resp, "structuredContent", None):
                                    payload = call_resp.structuredContent
                                else:
                                    payload = [
                                        getattr(c, "text", getattr(c, "data", str(c)))
                                        for c in (call_resp.content or [])
                                    ]
                            result.calls.append(
                                ToolCallResult(
                                    name=name,
                                    duration_s=time.time() - tc_start,
                                    ok=ok,
                                    payload=payload,
                                )
                            )
                        except Exception as e:  # noqa: BLE001
                            result.calls.append(
                                ToolCallResult(
                                    name=name,
                                    duration_s=time.time() - tc_start,
                                    ok=False,
                                    error=str(e),
                                )
                            )
        except Exception as e:  # noqa: BLE001
            result.fatal = str(e)
        finally:
            result.duration_s = time.time() - t_start

    asyncio.run(_run())
    return result
