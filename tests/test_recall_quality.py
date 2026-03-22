"""Hard test cases for recall quality — uses real EmbeddingEngine.

These tests verify that the recall pipeline returns semantically correct
results for challenging queries: multi-attribute, keyword-critical,
cross-entity, synonym, and fragmented data.

Marked with @pytest.mark.slow since they load the real embedding model.
Run with: pytest tests/test_recall_quality.py -v
"""

from __future__ import annotations

import pytest

from memory_core.ceo_db import CeoDB
from memory_core.ceo_retrieval import ceo_search
from memory_core.models import Decision, Episode, Fact, Principle


@pytest.fixture(scope="module")
def real_emb():
    """Load real EmbeddingEngine once for all tests in this module."""
    from memory_core.embedding import EmbeddingEngine
    emb = EmbeddingEngine()
    emb.load()
    return emb


@pytest.fixture()
def ceo_db():
    """Fresh in-memory CeoDB for each test."""
    db = CeoDB(":memory:")
    return db


def _insert_fact(db, emb, content, category="infrastructure", domain="ops",
                 project_id="", tags=None, entities=None):
    f = Fact(
        content=content, category=category, domain=domain,
        project_id=project_id,
        tags=tags or [], entities=entities or [],
        embedding=emb.encode_document(content),
    )
    db.insert_fact(f)
    return f


def _insert_decision(db, emb, title, choice, reasoning="", domain="tech",
                     project_id=""):
    embed_text = f"{title} {choice} {reasoning}"
    d = Decision(
        title=title, choice=choice, reasoning=reasoning, domain=domain,
        project_id=project_id,
        embedding=emb.encode_document(embed_text),
    )
    db.insert_decision(d)
    return d


def _insert_episode(db, emb, content, user_intent="", domain="tech",
                    project_id=""):
    e = Episode(
        content=content, user_intent=user_intent, domain=domain,
        project_id=project_id,
        embedding=emb.encode_document(content),
    )
    db.insert_episode(e)
    return e


def _insert_principle(db, emb, content, confidence=0.8, domain="tech",
                      project_id=""):
    p = Principle(
        content=content, confidence=confidence, domain=domain,
        project_id=project_id,
        embedding=emb.encode_document(content),
    )
    db.insert_principle(p)
    return p


def _recall(db, emb, query, top_k=5):
    """Run ceo_search and return results."""
    return ceo_search(db, emb, query, top_k=top_k)


def _ids(results):
    """Extract IDs from results for assertion."""
    return [r["id"] for r in results]


# ======================================================================
# Test Case 1: Multi-attribute fact lookup (the original bug)
# ======================================================================

@pytest.mark.slow
def test_multi_attribute_fact_ranked_first(ceo_db, real_emb):
    """A fact containing deployment + login + account info should rank above
    a fact that merely mentions the same project name."""
    fact_deploy = _insert_fact(
        ceo_db, real_emb,
        "OpenClaw deployed on mini server. Login: ssh tong@mini. "
        "Tailscale IP: 100.86.126.80. Username: tong.",
        entities=["OpenClaw", "mini", "tong", "100.86.126.80", "tong@mini"],
    )
    _insert_fact(
        ceo_db, real_emb,
        "OpenClaw provider group configured with Packy API key "
        "for Claude model access via packy-opus46-2.",
        entities=["OpenClaw", "Packy"],
    )
    _insert_fact(
        ceo_db, real_emb,
        "OpenClaw repo at github.com/auxten/openclaw, Python asyncio framework.",
    )

    results = _recall(ceo_db, real_emb, "OpenClaw 部署在哪里，怎么登录，用哪个账号")
    assert results, "Expected non-empty results"
    assert results[0]["id"] == fact_deploy.id, (
        f"Deployment fact should rank #1, got: {results[0]['content'][:80]}"
    )


# ======================================================================
# Test Case 2: Keyword-critical recall
# ======================================================================

@pytest.mark.slow
def test_keyword_critical_ip_lookup(ceo_db, real_emb):
    """Fact with literal IP address should rank above a decision about the same host."""
    fact_ip = _insert_fact(
        ceo_db, real_emb,
        "mini server IP: 192.168.1.100, SSH port 22, user tong",
        entities=["192.168.1.100", "mini", "tong"],
    )
    _insert_decision(
        ceo_db, real_emb,
        title="Use mini as primary deployment target",
        choice="All personal projects deploy to mini via SSH",
        reasoning="mini has 32GB RAM and is always on",
    )

    results = _recall(ceo_db, real_emb, "mini 服务器的 IP 地址是什么")
    assert results, "Expected non-empty results"
    assert results[0]["id"] == fact_ip.id, (
        f"IP fact should rank #1, got: {results[0]['content'][:80]}"
    )


# ======================================================================
# Test Case 3: Chinese synonym/paraphrase
# ======================================================================

@pytest.mark.slow
def test_chinese_synonym_paraphrase(ceo_db, real_emb):
    """Query using '目录' should match fact containing 'path'."""
    fact_path = _insert_fact(
        ceo_db, real_emb,
        "ClickMem database path: ~/.openclaw/memory/chdb-data",
        entities=["~/.openclaw/memory/chdb-data"],
    )
    _insert_principle(
        ceo_db, real_emb,
        "Always use local chDB storage, never cloud databases",
    )

    results = _recall(ceo_db, real_emb, "clickmem 的数据库文件存在哪个目录下")
    assert results, "Expected non-empty results"
    assert results[0]["id"] == fact_path.id, (
        f"Path fact should rank #1, got: {results[0]['content'][:80]}"
    )


# ======================================================================
# Test Case 4: Cross-entity query
# ======================================================================

@pytest.mark.slow
def test_cross_entity_query(ceo_db, real_emb):
    """Query needing both a decision and a fact should return both in top-3."""
    decision = _insert_decision(
        ceo_db, real_emb,
        title="Use Qwen3-Embedding-0.6B for all embeddings",
        choice="Qwen3-Embedding-0.6B with 256-dim MRL truncation",
        reasoning="Best tradeoff of quality vs speed for local embedding",
    )
    fact_loc = _insert_fact(
        ceo_db, real_emb,
        "Embedding model stored at ~/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B",
        entities=["~/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B"],
    )

    results = _recall(ceo_db, real_emb,
                      "embedding model 选的哪个，存在哪里", top_k=5)
    top3_ids = _ids(results[:3])
    assert decision.id in top3_ids, "Decision about model choice should be in top-3"
    assert fact_loc.id in top3_ids, "Fact about model location should be in top-3"


# ======================================================================
# Test Case 5: Fragmented info across multiple records
# ======================================================================

@pytest.mark.slow
def test_fragmented_facts(ceo_db, real_emb):
    """Both hook location facts should appear in top-5 for a general query."""
    fact_a = _insert_fact(
        ceo_db, real_emb,
        "Cursor hooks installed at ~/.cursor/hooks/clickmem",
        entities=["~/.cursor/hooks/clickmem"],
    )
    fact_b = _insert_fact(
        ceo_db, real_emb,
        "Claude Code hooks installed via plugin at ~/.clickmem/claude-plugin",
        entities=["~/.clickmem/claude-plugin"],
    )
    _insert_episode(
        ceo_db, real_emb,
        "Installed hooks for both Cursor and Claude Code to auto-ingest conversations",
        user_intent="Setup memory ingestion hooks",
    )

    results = _recall(ceo_db, real_emb, "hooks 装在哪里", top_k=5)
    top5_ids = _ids(results[:5])
    assert fact_a.id in top5_ids, "Cursor hooks fact should be in top-5"
    assert fact_b.id in top5_ids, "Claude Code hooks fact should be in top-5"


# ======================================================================
# Test Case 6: Different terminology
# ======================================================================

@pytest.mark.slow
def test_different_terminology(ceo_db, real_emb):
    """Query using '密码保护' should find fact about 'authentication/API key'."""
    fact_auth = _insert_fact(
        ceo_db, real_emb,
        "API authentication: set CLICKMEM_API_KEY environment variable before starting server",
        entities=["CLICKMEM_API_KEY"],
    )
    _insert_decision(
        ceo_db, real_emb,
        title="Implement bearer token auth for LAN REST API",
        choice="Bearer token via Authorization header",
        reasoning="Simple, stateless, sufficient for LAN security",
    )

    results = _recall(ceo_db, real_emb, "怎么给 clickmem 加密码保护", top_k=5)
    top3_ids = _ids(results[:3])
    assert fact_auth.id in top3_ids, (
        f"Auth fact should be in top-3, got: {[r['content'][:60] for r in results[:3]]}"
    )


# ======================================================================
# Test Case 7: Negation context
# ======================================================================

@pytest.mark.slow
def test_negation_context(ceo_db, real_emb):
    """Decision about NOT using sqlite-vec should rank for negative query."""
    decision = _insert_decision(
        ceo_db, real_emb,
        title="Do NOT use sqlite-vec, use chDB instead",
        choice="chDB (embedded ClickHouse)",
        reasoning="sqlite-vec has limited SQL support and no FINAL semantics",
    )
    _insert_fact(
        ceo_db, real_emb,
        "sqlite-vec was evaluated and rejected due to limited SQL support",
        entities=["sqlite-vec", "chDB"],
    )

    results = _recall(ceo_db, real_emb, "我们为什么不用 sqlite-vec", top_k=5)
    top3_ids = _ids(results[:3])
    assert decision.id in top3_ids, "Rejection decision should be in top-3"
