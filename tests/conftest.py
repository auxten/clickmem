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

import pytest

from memory_core.models import Memory, RetrievalConfig
from tests.helpers.mock_embedding import MockEmbeddingEngine
from tests.helpers.mock_llm import MockLLMComplete
from tests.helpers.factories import (
    MemoryFactory,
    seed_working,
    seed_episodic,
    seed_semantic,
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


@pytest.fixture(autouse=True)
def _reset_factory():
    """Reset the MemoryFactory counter and CLI/transport/LLM singletons between tests."""
    MemoryFactory.reset()
    import memory_core.cli as cli_mod
    cli_mod._db_instance = None
    cli_mod._transport_instance = None
    cli_mod._remote_url = None
    cli_mod._remote_api_key = None
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
