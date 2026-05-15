"""Embedding wrapper around Qwen3-Embedding-0.6B (default, 256 d).

The server is required to run zero LLMs. The embedding model is the **only**
model loaded in-process. This wrapper:

- Lazily imports ``sentence_transformers`` so cold imports stay fast.
- Forces ``device='cpu'`` (per project rules: never MPS on Apple Silicon).
- Truncates / normalises output vectors to exactly ``embedding_dim`` floats.
- Exposes a ``MockEmbeddingEngine`` for unit tests so the test layer can run
  without downloading model weights.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from typing import List, Sequence

from clickmem.config import get_config


_log = logging.getLogger(__name__)


class EmbeddingEngine:
    """Real sentence-transformers backed embedder."""

    def __init__(self, model_name: str | None = None, dim: int | None = None) -> None:
        cfg = get_config()
        self.model_name = model_name or cfg.embedding_model
        self.dim = int(dim or cfg.embedding_dim)
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer  # type: ignore

                _log.info("loading embedding model %s on CPU", self.model_name)
                self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    def encode(self, text: str) -> List[float]:
        model = self._ensure_model()
        vec = model.encode([text or ""], convert_to_numpy=True, normalize_embeddings=True)[0]
        out = [float(x) for x in vec[: self.dim]]
        # Pad with zeros if model emits fewer dims than configured (rare).
        if len(out) < self.dim:
            out.extend([0.0] * (self.dim - len(out)))
        return out

    def encode_batch(self, texts: Sequence[str]) -> List[List[float]]:
        model = self._ensure_model()
        vecs = model.encode(list(texts), convert_to_numpy=True, normalize_embeddings=True)
        out: List[List[float]] = []
        for v in vecs:
            row = [float(x) for x in v[: self.dim]]
            if len(row) < self.dim:
                row.extend([0.0] * (self.dim - len(row)))
            out.append(row)
        return out


class MockEmbeddingEngine:
    """Deterministic hash-based embedder for tests / smoke runs.

    Produces normalised vectors of the configured dimension so that recall
    semantics work without downloading a real model.
    """

    def __init__(self, dim: int | None = None) -> None:
        self.dim = int(dim or get_config().embedding_dim)

    def encode(self, text: str) -> List[float]:
        text = text or ""
        return _hashed_vector(text, self.dim)

    def encode_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.encode(t) for t in texts]


def _hashed_vector(text: str, dim: int) -> List[float]:
    out: List[float] = []
    seed = text.encode("utf-8")
    i = 0
    while len(out) < dim:
        h = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        for j in range(0, len(h), 2):
            if len(out) >= dim:
                break
            n = int.from_bytes(h[j : j + 2], "big") / 65535.0
            out.append((n * 2.0) - 1.0)
        i += 1
    norm = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / norm for v in out]


_engine: EmbeddingEngine | MockEmbeddingEngine | None = None
_engine_lock = threading.Lock()


def get_embedder() -> EmbeddingEngine | MockEmbeddingEngine:
    """Return the process-wide embedder singleton.

    Tests may inject a ``MockEmbeddingEngine`` via :func:`set_embedder`.
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = EmbeddingEngine()
    return _engine


def set_embedder(engine) -> None:
    """Override the global embedder. Intended for tests."""
    global _engine
    with _engine_lock:
        _engine = engine


def embed(text: str) -> List[float]:
    return get_embedder().encode(text)


def embed_batch(texts: Sequence[str]) -> List[List[float]]:
    return get_embedder().encode_batch(texts)
