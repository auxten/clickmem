"""MCP server smoke: tool catalogue + selected tool calls go through the
domain via :class:`LocalTransport`.

The MCP server exposes tools via ``FastMCP``. We don't spin up a real stdio
transport; instead we instantiate the server, enumerate its registered
tools, and exercise the underlying handlers directly through the
``LocalTransport`` they all call into.
"""

from __future__ import annotations

import pytest

from clickmem import memories
from clickmem.transport import LocalTransport


def test_mcp_server_lists_expected_tools(backend):
    from clickmem.mcp_server import _server

    server = _server()
    # FastMCP keeps tools in `_tool_manager._tools` (or `.tools` depending on
    # version) — fall through whichever attribute exists.
    tm = getattr(server, "_tool_manager", None) or server
    tools_attr = getattr(tm, "_tools", None) or getattr(tm, "tools", None)
    assert tools_attr is not None
    names = set(getattr(tools_attr, "keys", lambda: tools_attr)())
    if not isinstance(names, set):
        names = {t.name for t in tools_attr}
    expected = {
        "clickmem_remember",
        "clickmem_edit",
        "clickmem_forget",
        "clickmem_pin",
        "clickmem_blacklist",
        "clickmem_recall",
        "clickmem_recall_trace",
        "clickmem_show",
        "clickmem_list",
        "clickmem_conflicts",
        "clickmem_resolve",
        "clickmem_get_raw",
        "clickmem_project",
        "clickmem_review_dedup",
    }
    assert expected.issubset(names)


def test_mcp_tool_input_schemas_have_required_fields(backend):
    """Every registered tool advertises an inputSchema FastMCP can validate."""
    from clickmem.mcp_server import _server

    server = _server()
    tm = getattr(server, "_tool_manager", None) or server
    tools = getattr(tm, "_tools", None) or getattr(tm, "tools", None)
    items = list(tools.values()) if hasattr(tools, "values") else list(tools)
    assert items
    for tool in items:
        params = getattr(tool, "parameters", None)
        # mcp ToolParameters defines a JSON schema dict / pydantic model
        assert params is not None
        if getattr(tool, "name", "") == "clickmem_remember":
            assert {"content", "project_id", "tags"}.issubset(set(params.get("required", [])))


def test_clickmem_remember_through_transport(backend):
    """``clickmem_remember`` is a thin wrapper around ``LocalTransport.remember``."""
    out = LocalTransport().remember("mcp smoke add", kind="fact", project_id="p1", tags=["test"])
    assert out["status"] == "added"
    assert memories.get(out["id"]) is not None


def test_clickmem_recall_through_transport(backend):
    LocalTransport().remember(
        "mcp smoke recall fixture",
        kind="fact",
        project_id="p1",
        privacy="public",
        tags=["test"],
    )
    out = LocalTransport().recall("mcp smoke recall fixture", project_id="p1", tags=["test"], limit=5)
    assert out["hits"]
    assert out["hits"][0]["tag_match_count"] == 1


def test_clickmem_recall_timeout_fails_open_through_transport(monkeypatch, backend):
    from clickmem import recall as recall_mod

    def broken_recall(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr(recall_mod, "recall", broken_recall)
    out = LocalTransport().recall("mcp smoke timeout", project_id="p1", timeout_seconds=5.0)
    assert out["hits"] == []
    assert out["timeout"] is True


def test_clickmem_show_through_transport(backend):
    res = LocalTransport().remember("show me", kind="fact", project_id="p1", tags=["test"])
    show = LocalTransport().show(res["id"], with_history=True)
    assert show["memory"]["content"] == "show me"
    assert show["history"]


def test_clickmem_conflicts_through_transport(backend):
    conflicts = LocalTransport().conflicts(project_id="p1")
    assert isinstance(conflicts, list)
