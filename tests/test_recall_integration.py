"""Integration test cases for recall quality against the live server.

These tests run against the real remote clickmem server and verify that
recall returns relevant results for realistic user queries. Each case
has probe words that MUST appear in the top-N results.

Run with: pytest tests/test_recall_integration.py -v --run-integration
Requires: CLICKMEM_SERVER_HOST or a running server at 100.86.126.80:9527

These cases were accumulated from real debugging sessions and user
interactions. They serve as a regression suite for recall quality.
"""

from __future__ import annotations

import os
import pytest

# Skip unless explicitly opted in
pytestmark = pytest.mark.skipunless(
    os.environ.get("RUN_INTEGRATION") or pytest.config.getoption("--run-integration", default=False) if hasattr(pytest, "config") else os.environ.get("RUN_INTEGRATION"),
    "Integration tests require --run-integration or RUN_INTEGRATION=1",
)


def _recall(query: str, top_k: int = 5) -> list[dict]:
    """Call recall via the remote server."""
    import httpx
    host = os.environ.get("CLICKMEM_SERVER_HOST", "100.86.126.80")
    port = os.environ.get("CLICKMEM_SERVER_PORT", "9527")
    resp = httpx.post(
        f"http://{host}:{port}/v1/recall",
        json={"query": query, "top_k": top_k},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json().get("memories", [])


def _top_n_content(results: list[dict], n: int = 3) -> str:
    """Concatenate content of top-N results for probe word checking."""
    parts = []
    for r in results[:n]:
        parts.append(r.get("content", ""))
        meta = r.get("metadata", {})
        if meta.get("reasoning"):
            parts.append(meta["reasoning"])
    return " ".join(parts).lower()


def _has_any_probe(text: str, probes: list[str]) -> list[str]:
    """Return which probe words were found in text."""
    text_lower = text.lower()
    return [p for p in probes if p.lower() in text_lower]


def _missing_probes(text: str, probes: list[str]) -> list[str]:
    """Return which probe words were NOT found in text."""
    text_lower = text.lower()
    return [p for p in probes if p.lower() not in text_lower]


# ======================================================================
# Infrastructure & Deployment
# ======================================================================

class TestInfraRecall:
    """Recall tests for infrastructure and deployment knowledge."""

    def test_openclaw_deployment_info(self):
        """User asks where OpenClaw is deployed and how to login."""
        results = _recall("OpenClaw 部署在哪台机器上，怎么 SSH 登录", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["tong", "mini", "100.86.126.80"])
        assert len(found) >= 2, f"Expected >=2 probes, found {found} in top-3"

    def test_mini_ssh_username(self):
        """User asks what username to SSH with."""
        results = _recall("登录 mini 主机用什么用户名", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["tong"]), f"'tong' not in top-3"

    def test_tailscale_ip(self):
        """User asks for the Tailscale IP."""
        results = _recall("mini 的 Tailscale IP 地址", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["100.86.126.80"]), "IP not in top-3"

    def test_ssh_command(self):
        """User forgot the SSH command."""
        results = _recall("ssh 到 mini 的命令", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["tong@mini", "tong", "100.86.126.80"])
        assert len(found) >= 1, f"SSH info not in top-3, found: {found}"

    def test_server_port(self):
        """User asks what port the server runs on."""
        results = _recall("clickmem server 跑在哪个端口", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["9527"]), "Port 9527 not in top-3"

    def test_clickhouse_cloud_port(self):
        """User connecting to ClickHouse Cloud."""
        results = _recall("ClickHouse Cloud 的端口号是多少", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["9440", "secure"])
        assert len(found) >= 1, f"CH Cloud port info not in top-3"

    def test_cdp_chrome_port(self):
        """User asks about Chrome CDP port."""
        results = _recall("Chrome CDP 用的哪个端口", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["18800", "18801"]), "CDP port not in top-3"


# ======================================================================
# Bug Fixes & Debugging History
# ======================================================================

class TestBugRecall:
    """Recall tests for past bug diagnoses and fixes."""

    def test_whatsapp_crash_fix(self):
        """User debugging WhatsApp crash, wants to recall the fix."""
        results = _recall("WhatsApp channel 之前崩溃是怎么修的", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["extradirs", "openclaw.json", "skills"])
        assert len(found) >= 1, f"WhatsApp fix info not in top-3"

    def test_botschat_image_bug(self):
        """User recalls botschat image sending failure."""
        results = _recall("botschat 发图片失败是什么原因", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["mediaurls", "deliver"]), "Image bug not in top-3"

    def test_chdb_del_issue(self):
        """User recalls chdb Connection.__del__ race condition."""
        results = _recall("chdb Connection 的 __del__ 之前出了什么问题", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["__del__", "lifecycle"]), "__del__ issue not in top-3"

    def test_appsflyer_revenue_fix(self):
        """User recalls AppsFlyer revenue tracking bug."""
        results = _recall("AppsFlyer 收入没上报是怎么修的", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["tracksubscription"]), "AppsFlyer fix not in top-3"

    def test_botchat_restart_loop(self):
        """User recalls BotChat restart loop issue."""
        results = _recall("BotChat 一直重启是怎么回事", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["state sync", "health monitor", "restart"])
        assert len(found) >= 1, f"Restart loop info not in top-3"

    def test_wrangler_cron_not_stopped(self):
        """User recalls Cloudflare Workers cron trigger persistence."""
        results = _recall("wrangler 部署后旧的 cron trigger 还在跑", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["crons = []", "commenting out", "does not delete"])
        assert len(found) >= 1, f"Cron fix not in top-3"


# ======================================================================
# Decisions & Strategy
# ======================================================================

class TestDecisionRecall:
    """Recall tests for past decisions and strategy changes."""

    def test_x_posting_strategy(self):
        """User asks about the X posting strategy pivot."""
        results = _recall("X 上发帖的策略后来改成什么了", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["reply", "engagement", "disable"])
        assert len(found) >= 1, f"X strategy not in top-3"

    def test_plugin_install_coexistence(self):
        """User asks why settings.json hooks are kept alongside plugin."""
        results = _recall("安装 plugin 时为什么不能删除 settings.json", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["coexist", "inline", "settings.json"])
        assert len(found) >= 1, f"Hook coexistence not in top-3"


# ======================================================================
# Principles & Guidelines
# ======================================================================

class TestPrincipleRecall:
    """Recall tests for development principles and guidelines."""

    def test_product_bug_principle(self):
        """User asks about how to handle bugs in own products."""
        results = _recall("我们开发产品遇到 bug 应该怎么处理", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["product", "opportunity", "bug less"])
        assert len(found) >= 1, f"Product bug principle not in top-3"

    def test_agent_development_principles(self):
        """User asks about agent development principles."""
        results = _recall("我的 agent 开发原则", top_k=5)
        content = _top_n_content(results, 5)
        found = _has_any_probe(content, [
            "agent", "knowledge scope", "decision tree",
            "step-by-step", "workflow", "prompt",
        ])
        assert len(found) >= 2, f"Agent principles not well represented, found: {found}"

    def test_env_var_configuration(self):
        """User asks about configuration approach."""
        results = _recall("配置应该用环境变量还是配置文件", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["environment variable"]), "Env var principle not in top-3"


# ======================================================================
# Project Knowledge
# ======================================================================

class TestProjectRecall:
    """Recall tests for project-level knowledge."""

    def test_supported_agents(self):
        """User asks which AI agents clickmem supports."""
        results = _recall("clickmem 支持哪些 AI coding agent", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["claude code", "cursor", "openclaw"])
        assert len(found) >= 2, f"Agent list incomplete, found: {found}"

    def test_botschat_rebuild(self):
        """User needs to rebuild botschat plugin."""
        results = _recall("改了 botschat 源码之后怎么 rebuild", top_k=3)
        content = _top_n_content(results, 3)
        assert _has_any_probe(content, ["dist", "channel.js"]), "Rebuild info not in top-3"

    def test_ainote_recording_button(self):
        """User recalls AiNote recording button optimization."""
        results = _recall("AiNote 录音按钮不够明显怎么优化的", top_k=3)
        content = _top_n_content(results, 3)
        found = _has_any_probe(content, ["animation", "glow", "recording"])
        assert len(found) >= 1, f"AiNote UI not in top-3"


# ======================================================================
# Identity
# ======================================================================

class TestIdentityRecall:
    """Recall tests for user identity information."""

    def test_user_name(self):
        """User asks for their own name."""
        results = _recall("我叫什么名字", top_k=5)
        content = _top_n_content(results, 5)
        found = _has_any_probe(content, ["auxten", "pengcheng"])
        assert len(found) >= 1, f"User name not in top-5: {[r.get('content','')[:60] for r in results[:5]]}"
