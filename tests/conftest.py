"""Shared pytest fixtures.

Goals:

* Every test gets a brand-new chDB directory and a brand-new backend
  singleton — no cross-test leakage.
* Never load the real Qwen embedding model; inject the deterministic
  :class:`MockEmbeddingEngine` instead.
* Provide a ``client`` fixture that wraps the FastAPI app in an
  ``httpx.AsyncClient`` for integration tests.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest

# Make sure the package can be imported without an `.[dev]` install having
# rewritten `sys.path` (handy when pytest is invoked from a fresh shell).
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path) -> Iterator[Path]:
    """Pin every test to a fresh chDB directory and the local backend."""
    db_dir = tmp_path / f"chdb-{uuid.uuid4().hex[:8]}"
    db_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLICKMEM_DB_PATH", str(db_dir))
    monkeypatch.setenv("CLICKMEM_BACKEND", "local")
    monkeypatch.setenv("CLICKMEM_LOG_LEVEL", "ERROR")
    monkeypatch.delenv("CLICKMEM_REMOTE", raising=False)
    monkeypatch.delenv("CLICKMEM_API_KEY", raising=False)

    # Reset cached singletons that may have been touched by previous tests.
    from clickmem import backend as backend_mod
    from clickmem import config as config_mod
    from clickmem import embedding as embedding_mod

    config_mod._cached = None
    backend_mod.reset_backend()
    embedding_mod._engine = None

    # Inject the deterministic test embedder so no model weights are loaded.
    from clickmem.embedding import MockEmbeddingEngine, set_embedder
    set_embedder(MockEmbeddingEngine(dim=config_mod.get_config(refresh=True).embedding_dim))

    yield db_dir

    # Tear-down: close the backend so the chDB directory can be cleaned.
    backend_mod.reset_backend()
    embedding_mod._engine = None


@pytest.fixture
def backend():
    """Return the fresh test-scoped backend (bootstraps schema on first call)."""
    from clickmem.backend import get_backend
    return get_backend(refresh=True)


@pytest.fixture
def app(backend):
    """Build a fresh FastAPI app bound to the per-test backend."""
    from clickmem.server import create_app
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator:
    """Async httpx client for integration tests."""
    import httpx
    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
