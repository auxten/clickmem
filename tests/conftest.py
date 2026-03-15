"""Shared pytest fixtures for the clickmem test suite.

Note: chDB's EmbeddedServer can only be initialized with one persistent path
per process. All test fixtures use in-memory sessions (`:memory:`) with
table truncation for isolation between tests.
"""

from __future__ import annotations

import os

# Force CLI to use in-memory DB during tests
os.environ["CLICKMEM_DB_PATH"] = ":memory:"
# Use local mode so get_llm_complete() returns None when no model is available
# (rather than returning a litellm function that fails at call time)
os.environ.setdefault("CLICKMEM_LLM_MODE", "local")
# Prevent auto-selection from loading a real (multi-GB) model during tests
os.environ.setdefault("CLICKMEM_LOCAL_MODEL", "Qwen/Qwen3.5-2B")

import pytest

from memory_core.models import Memory, RetrievalConfig
from tests.helpers.mock_embedding import MockEmbeddingEngine
from tests.helpers.mock_llm import MockLLMComplete
from tests.helpers.factories import (
    DecisionFactory,
    EpisodeFactory,
    MemoryFactory,
    PrincipleFactory,
    ProjectFactory,
    seed_decisions,
    seed_episodes,
    seed_episodic,
    seed_principles,
    seed_projects,
    seed_semantic,
    seed_working,
)


@pytest.fixture
def tmp_db_path():
    """Provide a db path. Returns ':memory:' to avoid chDB singleton conflicts."""
    return ":memory:"


@pytest.fixture
def db():
    """Create a fresh MemoryDB instance (in-memory, isolated via TRUNCATE)."""
    from memory_core import MemoryDB

    instance = MemoryDB(":memory:")
    instance._truncate()
    return instance


@pytest.fixture
def mock_emb():
    """Provide a MockEmbeddingEngine (pre-loaded)."""
    engine = MockEmbeddingEngine(dimension=256)
    engine.load()
    return engine


@pytest.fixture
def mock_llm():
    """Provide a fresh MockLLMComplete instance."""
    return MockLLMComplete()


@pytest.fixture
def populated_db(db, mock_emb):
    """MemoryDB pre-populated with L0 + L1 + L2 seed data.

    Contents:
      - 1 working memory (L0)
      - 5 episodic memories (L1)
      - 5 semantic memories (L2)
    """
    # L0
    working = seed_working()
    working.embedding = mock_emb.encode_document(working.content)
    db.insert(working)

    # L1
    for m in seed_episodic(5):
        m.embedding = mock_emb.encode_document(m.content)
        db.insert(m)

    # L2
    for m in seed_semantic(5):
        m.embedding = mock_emb.encode_document(m.content)
        db.insert(m)

    return db


@pytest.fixture
def workspace_path(tmp_path):
    """Provide a temporary workspace directory for .md export."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)


@pytest.fixture
def retrieval_config():
    """Default RetrievalConfig for testing."""
    return RetrievalConfig(
        top_k=10,
        w_vector=0.5,
        w_keyword=0.5,
        decay_days=60.0,
        mmr_lambda=0.7,
    )


# ---------------------------------------------------------------------------
# CEO Brain fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ceo_db():
    """Create a fresh CeoDB instance (in-memory, isolated via TRUNCATE)."""
    from memory_core.ceo_db import CeoDB

    instance = CeoDB(":memory:")
    instance._truncate()
    return instance


@pytest.fixture
def populated_ceo_db(ceo_db, mock_emb):
    """CeoDB pre-populated with projects, decisions, principles, episodes."""
    projects = seed_projects(2)
    for p in projects:
        p.embedding = mock_emb.encode_document(p.name + " " + p.description)
        ceo_db.insert_project(p)

    project_id = projects[0].id

    for d in seed_decisions(3, project_id=project_id):
        d.embedding = mock_emb.encode_document(d.title + " " + d.choice)
        ceo_db.insert_decision(d)

    for p in seed_principles(3, project_id=project_id):
        p.embedding = mock_emb.encode_document(p.content)
        ceo_db.insert_principle(p)

    for e in seed_episodes(5, project_id=project_id):
        e.embedding = mock_emb.encode_document(e.content)
        ceo_db.insert_episode(e)

    return ceo_db


_shared_cli_transport = None


def _get_shared_cli_transport():
    """Return a session-scoped LocalTransport with mock embedding for CLI tests.

    Uses MockEmbeddingEngine to avoid loading the real Qwen3-Embedding-0.6B
    model (~600M params, ~60s load time) which is unnecessary for unit tests.
    """
    global _shared_cli_transport
    if _shared_cli_transport is None:
        from memory_core.transport import LocalTransport
        _shared_cli_transport = LocalTransport()
        mock_emb = MockEmbeddingEngine(dimension=256)
        mock_emb.load()
        _shared_cli_transport._emb = mock_emb
    return _shared_cli_transport


@pytest.fixture(autouse=True)
def _reset_factory():
    """Reset the MemoryFactory counter and CLI/transport/LLM singletons between tests.

    CLI commands use ``get_transport()`` which now requires a running HTTP
    server.  For unit tests we bypass that by injecting a shared
    ``LocalTransport`` directly (backed by the in-memory chDB set via
    ``CLICKMEM_DB_PATH``).  The DB is truncated between tests for isolation;
    the embedding engine is reused to avoid reloading the model every time.
    """
    MemoryFactory.reset()
    ProjectFactory.reset()
    DecisionFactory.reset()
    PrincipleFactory.reset()
    EpisodeFactory.reset()
    shared_t = _get_shared_cli_transport()
    shared_t._get_db()._truncate()
    import memory_core.cli as cli_mod
    cli_mod._db_instance = None
    cli_mod._transport_instance = shared_t
    cli_mod._remote_url = None
    cli_mod._remote_api_key = None
    cli_mod._force_local = False
    import memory_core.server as server_mod
    server_mod._transport = None
    server_mod._api_key_env = None
    server_mod._debug_mode = False
    import memory_core.llm as llm_mod
    llm_mod._local_engine = None
    llm_mod._local_engine_failed = False
    yield
    MemoryFactory.reset()
    cli_mod._db_instance = None
    cli_mod._transport_instance = None
    server_mod._transport = None
    llm_mod._local_engine = None
    llm_mod._local_engine_failed = False
