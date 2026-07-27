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

from clickmem.config import get_config
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
        project_id: str,
        tags: list[str],
        kind: str = "free",
        privacy: str = "private",
        pinned: bool = False,
        source: str = "agent_remember",
        source_ref: str = "",
        revises_id: str = "",
        agent: str = "",
    ) -> dict[str, Any]:
        """Expand: commit a new memory.

        ``project_id`` is required. Pass a readable project slug such as
        ``auxten/clickmem`` for project memory, or the literal string
        ``global`` for global memory. ``tags`` must contain at least one
        management/search tag, e.g. ``["workflow", "deployment"]``.

        Returns ``{status, id, peer_ids, message}``. ``status`` is one of
        ``queued`` / ``added`` / ``merged`` / ``conflicted`` / ``rejected`` /
        ``refused`` — the calling agent should react to ``conflicted`` by
        either Revising the existing memory or contracting one.
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
        tags: Optional[list[str]] = None,
        tag_mode: str = "any",
        timeout_seconds: float = 5.0,
        agent: str = "",
    ) -> dict[str, Any]:
        """Run embedding recall.

        Pass ``project_id`` and 2-5 task tags at startup so recall can scope by
        project/tag before ranking by embedding similarity. ``tag_mode`` is
        ``any`` by default; pass ``all`` when every tag must be present.

        ``include_confidential`` is only honoured if ``privacy_ack=True``
        (extra guard before confidential rows leave the server).
        """
        if include_confidential and not privacy_ack:
            include_confidential = False
        return get_transport().recall(
            query,
            project_id=project_id,
            limit=limit,
            include_confidential=include_confidential,
            cross_project=cross_project,
            kind=kind,
            tags=list(tags or []),
            tag_mode=tag_mode,
            timeout_seconds=timeout_seconds,
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
        tags: Optional[list[str]] = None,
        tag_mode: str = "any",
        timeout_seconds: float = 5.0,
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
            tags=list(tags or []),
            tag_mode=tag_mode,
            timeout_seconds=timeout_seconds,
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
    def clickmem_store(op: str, id: str = "", description: str = "") -> dict[str, Any]:
        """Memory-store ops. ``op`` ∈ ``list`` / ``use`` / ``new``.

        A store is one whole memory database. ``list`` shows what exists for
        the configured backend, ``use`` switches (recorded per user, applies
        on next start), ``new`` creates a fresh empty store and switches.
        """
        from clickmem import stores

        op = (op or "").lower()
        if op == "list":
            return stores.store_list()
        if op == "use":
            return stores.store_use(id)
        if op == "new":
            return stores.store_new(id, description=description)
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

    Mounted by :mod:`clickmem.server` at ``/sse`` so the public URLs are::

        GET  /sse/             -> SSE event stream
        POST /sse/messages/    -> client-to-server messages

    FastMCP's ``sse_app`` defaults route the SSE handler at ``/sse`` inside
    the sub-app, which would double up to ``/sse/sse`` after our mount. We
    move the handler to ``/`` and pass ``mount_path="/sse"`` so the URL
    advertised in the SSE init event matches the public mount.

    DNS-rebinding allow-lists are taken from
    ``CLICKMEM_MCP_ALLOWED_HOSTS`` / ``CLICKMEM_MCP_ALLOWED_ORIGINS`` (default
    ``["*"]`` so LAN/Tailscale hosts work without extra configuration).
    """
    server = _server()

    server.settings.sse_path = "/"

    cfg = get_config()
    sec = server.settings.transport_security
    hosts = list(cfg.mcp_allowed_hosts)
    origins = list(cfg.mcp_allowed_origins)
    # FastMCP's TransportSecurityMiddleware only knows literal hosts and
    # ":*" port wildcards — it has no "any host" pattern. When the operator
    # asks for "*" (also the default so LAN/Tailscale clients work), turn the
    # DNS-rebinding check off entirely. Otherwise enforce the configured list.
    if "*" in hosts and "*" in origins:
        sec.enable_dns_rebinding_protection = False
    else:
        sec.enable_dns_rebinding_protection = True
        sec.allowed_hosts = [h for h in hosts if h != "*"]
        sec.allowed_origins = [o for o in origins if o != "*"]

    # Don't pass ``mount_path`` here. SseServerTransport already prefixes the
    # advertised messages URL with the ASGI ``root_path`` (which Starlette sets
    # to "/sse" for us). Passing mount_path would bake "/sse" into _endpoint
    # too and produce "/sse/sse/messages/" in the SSE init event.
    return server.sse_app()


def main_stdio() -> None:  # console-script entry: ``clickmem-mcp``
    """Run the MCP tools over stdio."""
    server = _server()
    server.run()


__all__ = ["build_sse_app", "main_stdio"]
