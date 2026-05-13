"""MCP server: one tool per parity-table row, plus the agent-side
``clickmem_review_dedup`` helper.

Two ways to run it:

- The FastAPI server mounts an SSE app at ``/sse`` via :func:`build_sse_app`.
- ``clickmem-mcp`` (the console script) runs the same tools over stdio for
  agents (e.g. Claude Desktop) that want a local subprocess transport.

Every tool just calls into :mod:`clickmem.transport`, so behaviour stays
identical whether the agent talks to a local in-process backend or a remote
HTTP server via ``CLICKMEM_REMOTE``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from clickmem.transport import get_transport


_log = logging.getLogger(__name__)


def _server() -> FastMCP:
    server = FastMCP("clickmem")
    _register_tools(server)
    return server


def _register_tools(server: FastMCP) -> None:
    @server.tool()
    def clickmem_remember(
        content: str,
        kind: str = "free",
        project_id: str = "",
        privacy: str = "private",
        tags: Optional[list[str]] = None,
        pinned: bool = False,
        source: str = "agent_remember",
        source_ref: str = "",
        revises_id: str = "",
        agent: str = "",
    ) -> dict[str, Any]:
        """Expand: commit a new memory.

        Returns ``{status, id, peer_ids, message}``. ``status`` is one of
        ``added`` / ``merged`` / ``conflicted`` / ``rejected`` / ``refused`` —
        the calling agent should react to ``conflicted`` by either Revising
        the existing memory or contracting one.
        """
        return get_transport().remember(
            content,
            kind=kind,
            project_id=project_id,
            privacy=privacy,
            tags=list(tags or []),
            pinned=bool(pinned),
            source=source,
            source_ref=source_ref,
            revises_id=revises_id,
            agent=agent,
        )

    @server.tool()
    def clickmem_edit(
        memory_id: str,
        content: Optional[str] = None,
        kind: Optional[str] = None,
        privacy: Optional[str] = None,
        project_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        pinned: Optional[bool] = None,
        revises_id: Optional[str] = None,
        agent: str = "",
    ) -> dict[str, Any]:
        """Revise: edit an existing memory; re-runs conflict detection."""
        payload: dict[str, Any] = {}
        for k, v in {
            "content": content,
            "kind": kind,
            "privacy": privacy,
            "project_id": project_id,
            "tags": tags,
            "pinned": pinned,
            "revises_id": revises_id,
        }.items():
            if v is not None:
                payload[k] = v
        payload["agent"] = agent
        return get_transport().edit(memory_id, **payload)

    @server.tool()
    def clickmem_forget(memory_id: str, reason: str = "", agent: str = "") -> dict[str, Any]:
        """Contract: mark a memory ``status='contracted'`` (excluded from recall)."""
        return get_transport().forget(memory_id, reason=reason, agent=agent)

    @server.tool()
    def clickmem_pin(memory_id: str, unpin: bool = False, agent: str = "") -> dict[str, Any]:
        """Reinforce: pin or unpin a memory (pass ``unpin=True`` to unpin)."""
        if unpin:
            return get_transport().unpin(memory_id, agent=agent)
        return get_transport().pin(memory_id, agent=agent)

    @server.tool()
    def clickmem_blacklist(
        op: str,
        pattern: str = "",
        scope: str = "global",
        reason: str = "",
        blacklist_id: str = "",
    ) -> dict[str, Any]:
        """Refuse: ``op`` is one of ``add`` / ``remove`` / ``list``."""
        tr = get_transport()
        op = (op or "").lower()
        if op == "add":
            return tr.blacklist_add(pattern, scope=scope, reason=reason)
        if op == "remove":
            return tr.blacklist_remove(blacklist_id or pattern)
        if op == "list":
            return {"items": tr.blacklist_list()}
        return {"status": "unknown_op", "op": op}

    @server.tool()
    def clickmem_recall(
        query: str,
        project_id: str = "",
        limit: int = 10,
        include_confidential: bool = False,
        privacy_ack: bool = False,
        cross_project: bool = False,
        kind: Optional[str] = None,
        agent: str = "",
    ) -> dict[str, Any]:
        """Run embedding recall. ``include_confidential`` is only honoured if
        ``privacy_ack=True`` (extra guard before confidential rows leave the
        server)."""
        if include_confidential and not privacy_ack:
            include_confidential = False
        return get_transport().recall(
            query,
            project_id=project_id,
            limit=limit,
            include_confidential=include_confidential,
            cross_project=cross_project,
            kind=kind,
            agent=agent,
        )

    @server.tool()
    def clickmem_recall_trace(
        query: str,
        project_id: str = "",
        limit: int = 10,
        include_confidential: bool = False,
        privacy_ack: bool = False,
        cross_project: bool = False,
        kind: Optional[str] = None,
        agent: str = "",
    ) -> dict[str, Any]:
        """Recall with per-candidate scoring breakdown (Recall Lab)."""
        if include_confidential and not privacy_ack:
            include_confidential = False
        return get_transport().recall_trace(
            query,
            project_id=project_id,
            limit=limit,
            include_confidential=include_confidential,
            cross_project=cross_project,
            kind=kind,
            agent=agent,
        )

    @server.tool()
    def clickmem_show(memory_id: str, with_history: bool = False, with_neighbors: bool = False) -> dict[str, Any]:
        """Return one memory; optionally also its history and neighbours."""
        return get_transport().show(memory_id, with_history=with_history, with_neighbors=with_neighbors)

    @server.tool()
    def clickmem_list(
        project_id: Optional[str] = None,
        privacy: Optional[str] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        pinned: Optional[bool] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Paginated list of memories with the full filter set."""
        return get_transport().list_memories(
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

    @server.tool()
    def clickmem_conflicts(project_id: Optional[str] = None) -> dict[str, Any]:
        """Return current unresolved conflict groups."""
        return {"items": get_transport().conflicts(project_id=project_id)}

    @server.tool()
    def clickmem_resolve(memory_id: str, op: str, peer_id: str = "") -> dict[str, Any]:
        """Resolve a conflict. ``op`` ∈ ``allow`` / ``contract`` / ``revise``."""
        return get_transport().resolve(memory_id, op, peer_id=peer_id)

    @server.tool()
    def clickmem_get_raw(
        session_id: Optional[str] = None,
        last: int = 50,
        agent: Optional[str] = None,
    ) -> dict[str, Any]:
        """Cold-storage retrieval. Raw transcripts are **never** part of recall."""
        return {"items": get_transport().get_raw(session_id=session_id, last=last, agent=agent)}

    @server.tool()
    def clickmem_project(op: str, a: str = "", b: str = "", reason: str = "") -> dict[str, Any]:
        """Project ops. ``op`` ∈ ``link`` / ``list``."""
        tr = get_transport()
        op = (op or "").lower()
        if op == "link":
            return tr.project_link(a, b, reason=reason)
        if op == "list":
            return {"items": tr.projects_list()}
        return {"status": "unknown_op", "op": op}

    @server.tool()
    def clickmem_review_dedup(candidate_id: str, neighbor_ids: list[str]) -> dict[str, Any]:
        """Return the candidate and its neighbours so the **agent's** model can
        decide MERGE / KEEP / SKIP. The server runs no LLM; this tool just
        bundles the rows for cheap comparison."""
        tr = get_transport()
        candidate = tr.show(candidate_id)
        neighbors = [tr.show(nid) for nid in (neighbor_ids or [])]
        return {"candidate": candidate, "neighbors": neighbors}


# ---------- public surface used by `server.py` and the stdio script -------


def build_sse_app():
    """Return an ASGI app exposing the MCP SSE endpoint at the root.

    Mounted by :mod:`clickmem.server` at ``/sse``.
    """
    server = _server()
    return server.sse_app()


def main_stdio() -> None:  # console-script entry: ``clickmem-mcp``
    """Run the MCP tools over stdio."""
    server = _server()
    server.run()


__all__ = ["build_sse_app", "main_stdio"]
