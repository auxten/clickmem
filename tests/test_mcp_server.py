"""Tests for the MCP server — tool handlers and resources.

Calls the MCP tool/resource handlers directly (no transport needed).
"""

from __future__ import annotations

import json

import pytest

try:
    from mcp.types import TextContent
    from memory_core.mcp_server import (
        call_tool,
        list_tools,
        list_resources,
        read_resource,
        set_transport,
        _get_transport,
    )
    import memory_core.mcp_server as mcp_mod
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp not installed")


@pytest.fixture(autouse=True)
def _reset_mcp_transport():
    """Use in-memory LocalTransport for each test, with clean DB."""
    from memory_core.transport import LocalTransport
    t = LocalTransport(db_path=":memory:")
    t._get_db()._truncate()
    mcp_mod._transport = t
    yield
    mcp_mod._transport = None


class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_all_tools(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "clickmem_recall" in names
        assert "clickmem_remember" in names
        assert "clickmem_extract" in names
        assert "clickmem_forget" in names
        assert "clickmem_status" in names
        assert "clickmem_working" in names

    @pytest.mark.asyncio
    async def test_tool_schemas_have_required_fields(self):
        tools = await list_tools()
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.inputSchema


class TestCallToolRemember:
    @pytest.mark.asyncio
    async def test_remember_semantic(self):
        result = await call_tool("clickmem_remember", {
            "content": "User prefers dark mode",
            "layer": "semantic",
            "category": "preference",
            "tags": ["ui"],
            "no_upsert": True,
        })
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["status"] == "stored"

    @pytest.mark.asyncio
    async def test_remember_episodic(self):
        result = await call_tool("clickmem_remember", {
            "content": "Decided on MCP for integration",
            "layer": "episodic",
            "category": "decision",
        })
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["status"] == "stored"

    @pytest.mark.asyncio
    async def test_remember_defaults(self):
        result = await call_tool("clickmem_remember", {
            "content": "Default layer and category",
        })
        data = json.loads(result[0].text)
        assert data["status"] == "stored"


class TestCallToolRecall:
    @pytest.mark.asyncio
    async def test_recall_empty(self):
        result = await call_tool("clickmem_recall", {"query": "anything"})
        assert len(result) == 1
        assert "No matching" in result[0].text

    @pytest.mark.asyncio
    async def test_recall_finds_stored(self):
        await call_tool("clickmem_remember", {
            "content": "Python is the main language",
            "layer": "semantic", "no_upsert": True,
        })
        result = await call_tool("clickmem_recall", {"query": "Python language"})
        assert len(result) == 1
        assert "Python" in result[0].text

    @pytest.mark.asyncio
    async def test_recall_with_top_k(self):
        for i in range(5):
            await call_tool("clickmem_remember", {
                "content": f"Item number {i}",
                "layer": "semantic", "no_upsert": True,
            })
        result = await call_tool("clickmem_recall", {
            "query": "Item number", "top_k": 2,
        })
        lines = result[0].text.strip().split("\n")
        assert len(lines) <= 2


class TestCallToolForget:
    @pytest.mark.asyncio
    async def test_forget_by_id(self):
        result = await call_tool("clickmem_remember", {
            "content": "Forget me", "layer": "semantic", "no_upsert": True,
        })
        data = json.loads(result[0].text)
        mid = data["id"]

        result = await call_tool("clickmem_forget", {"id_or_content": mid})
        data = json.loads(result[0].text)
        assert data["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_forget_not_found(self):
        result = await call_tool("clickmem_forget", {"id_or_content": "nonexistent"})
        data = json.loads(result[0].text)
        assert "error" in data


class TestCallToolStatus:
    @pytest.mark.asyncio
    async def test_status_empty(self):
        result = await call_tool("clickmem_status", {})
        data = json.loads(result[0].text)
        assert "counts" in data
        assert "total" in data
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_status_with_data(self):
        await call_tool("clickmem_remember", {
            "content": "Fact", "layer": "semantic", "no_upsert": True,
        })
        result = await call_tool("clickmem_status", {})
        data = json.loads(result[0].text)
        assert data["total"] >= 1


class TestCallToolWorking:
    @pytest.mark.asyncio
    async def test_working_set_and_get(self):
        result = await call_tool("clickmem_working", {"content": "Debugging auth"})
        data = json.loads(result[0].text)
        assert data["status"] == "stored"

        result = await call_tool("clickmem_working", {})
        assert "Debugging auth" in result[0].text

    @pytest.mark.asyncio
    async def test_working_empty(self):
        result = await call_tool("clickmem_working", {})
        assert "No working memory" in result[0].text


class TestCallToolExtract:
    @pytest.mark.asyncio
    async def test_extract(self, monkeypatch):
        monkeypatch.setattr("memory_core.llm.get_llm_complete", lambda: None)
        result = await call_tool("clickmem_extract", {
            "text": "user: I like using vim keybindings",
        })
        data = json.loads(result[0].text)
        assert "extracted" in data
        assert data["extracted"] >= 1


class TestCallToolUnknown:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = await call_tool("nonexistent_tool", {})
        assert "Unknown tool" in result[0].text


class TestResources:
    @pytest.mark.asyncio
    async def test_list_resources(self):
        resources = await list_resources()
        uris = {str(r.uri) for r in resources}
        assert "clickmem://status" in uris
        assert "clickmem://working" in uris

    @pytest.mark.asyncio
    async def test_read_status_resource(self):
        content = await read_resource("clickmem://status")
        data = json.loads(content)
        assert "counts" in data

    @pytest.mark.asyncio
    async def test_read_working_resource_empty(self):
        content = await read_resource("clickmem://working")
        assert content in ("(empty)", "None")

    @pytest.mark.asyncio
    async def test_read_working_resource_with_data(self):
        await call_tool("clickmem_working", {"content": "Test working"})
        content = await read_resource("clickmem://working")
        assert "Test working" in content

    @pytest.mark.asyncio
    async def test_read_unknown_resource(self):
        with pytest.raises(ValueError, match="Unknown resource"):
            await read_resource("clickmem://nonexistent")
