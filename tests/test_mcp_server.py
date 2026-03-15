"""Tests for the MCP server — CEO Brain tool handlers and resources.

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
    """Use in-memory LocalTransport with mock embedding for each test."""
    from memory_core.transport import LocalTransport
    from tests.helpers.mock_embedding import MockEmbeddingEngine
    t = LocalTransport(db_path=":memory:")
    t._get_db()._truncate()
    t._get_ceo_db()._truncate()
    mock_emb = MockEmbeddingEngine(dimension=256)
    mock_emb.load()
    t._emb = mock_emb
    mcp_mod._transport = t
    yield
    mcp_mod._transport = None


class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_all_tools(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "ceo_brief" in names
        assert "ceo_decide" in names
        assert "ceo_remember" in names
        assert "ceo_review" in names
        assert "ceo_retro" in names
        assert "ceo_portfolio" in names

    @pytest.mark.asyncio
    async def test_tool_schemas_have_required_fields(self):
        tools = await list_tools()
        for tool in tools:
            assert tool.name
            assert tool.description
            assert tool.inputSchema


class TestCEOBrief:
    @pytest.mark.asyncio
    async def test_brief_empty(self):
        result = await call_tool("ceo_brief", {})
        data = json.loads(result[0].text)
        assert "principles" in data
        assert "decisions" in data

    @pytest.mark.asyncio
    async def test_brief_with_project(self):
        # First store a project
        await call_tool("ceo_remember", {
            "type": "decision",
            "content": {"title": "Use chDB", "choice": "chDB", "reasoning": "Fast"},
        })
        result = await call_tool("ceo_brief", {"query": "chDB"})
        data = json.loads(result[0].text)
        assert "decisions" in data


class TestCEORemember:
    @pytest.mark.asyncio
    async def test_remember_decision(self):
        result = await call_tool("ceo_remember", {
            "type": "decision",
            "content": {
                "title": "Use Python",
                "choice": "Python 3.11",
                "reasoning": "Best ecosystem",
            },
        })
        data = json.loads(result[0].text)
        assert data["status"] == "stored"
        assert data["type"] == "decision"

    @pytest.mark.asyncio
    async def test_remember_principle(self):
        result = await call_tool("ceo_remember", {
            "type": "principle",
            "content": {
                "content": "Keep it simple",
                "domain": "tech",
                "confidence": 0.8,
            },
        })
        data = json.loads(result[0].text)
        assert data["status"] == "stored"
        assert data["type"] == "principle"

    @pytest.mark.asyncio
    async def test_remember_episode(self):
        result = await call_tool("ceo_remember", {
            "type": "episode",
            "content": {
                "content": "Set up database layer",
                "user_intent": "Build storage",
            },
        })
        data = json.loads(result[0].text)
        assert data["status"] == "stored"
        assert data["type"] == "episode"


class TestCEODecide:
    @pytest.mark.asyncio
    async def test_decide_returns_structure(self):
        result = await call_tool("ceo_decide", {
            "question": "Should we use SQLite or chDB?",
            "options": ["SQLite", "chDB"],
        })
        data = json.loads(result[0].text)
        assert "related_decisions" in data
        assert "relevant_principles" in data


class TestCEOReview:
    @pytest.mark.asyncio
    async def test_review_returns_structure(self):
        result = await call_tool("ceo_review", {
            "proposed_plan": "Migrate to cloud-based storage",
        })
        data = json.loads(result[0].text)
        assert "relevant_principles" in data
        assert "relevant_decisions" in data


class TestCEORetro:
    @pytest.mark.asyncio
    async def test_retro_returns_structure(self):
        result = await call_tool("ceo_retro", {
            "project_id": "test-project",
        })
        data = json.loads(result[0].text)
        assert "decision_summary" in data
        assert "validated" in data


class TestCEOPortfolio:
    @pytest.mark.asyncio
    async def test_portfolio_empty(self):
        result = await call_tool("ceo_portfolio", {})
        data = json.loads(result[0].text)
        assert "projects" in data
        assert "totals" in data

    @pytest.mark.asyncio
    async def test_portfolio_with_data(self):
        # Store a project via remember (as decision, to populate)
        await call_tool("ceo_remember", {
            "type": "decision",
            "content": {"title": "Test", "choice": "A"},
        })
        result = await call_tool("ceo_portfolio", {})
        data = json.loads(result[0].text)
        assert "totals" in data


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

    @pytest.mark.asyncio
    async def test_read_status_resource(self):
        content = await read_resource("clickmem://status")
        data = json.loads(content)
        assert "counts" in data or "total" in data

    @pytest.mark.asyncio
    async def test_read_unknown_resource(self):
        with pytest.raises(ValueError, match="Unknown resource"):
            await read_resource("clickmem://nonexistent")
