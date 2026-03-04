"""EmbeddingEngine — local vector embedding using sentence-transformers.

Default model: Qwen/Qwen3-Embedding-0.6B (1024d native, truncated to 256d)
"""

from __future__ import annotations

import math
from typing import Optional


class EmbeddingEngine:
    """Vector embedding engine backed by a local model.

    Default model: Qwen/Qwen3-Embedding-0.6B
    - ~600M parameters, runs locally on CPU/GPU
    - Native 1024d output, supports MRL truncation to 256d
    - Query encoding uses prompt_name="query" for instruction-tuned retrieval
    """

    def __init__(self, model_path: str = "Qwen/Qwen3-Embedding-0.6B", dimension: int = 256):
        self._model_path = model_path
        self._target_dimension = dimension
        self._model = None

    def load(self) -> None:
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(
            self._model_path,
            truncate_dim=self._target_dimension,
        )

    @property
    def dimension(self) -> int:
        return self._target_dimension

    def encode_query(self, text: str) -> list[float]:
        """Encode a query string with instruction prefix for retrieval."""
        assert self._model is not None, "Call load() first"
        vec = self._model.encode(text, prompt_name="query", normalize_embeddings=True)
        return vec.tolist()

    def encode_document(self, text: str) -> list[float]:
        """Encode a document string (no instruction prefix)."""
        assert self._model is not None, "Call load() first"
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of document strings."""
        assert self._model is not None, "Call load() first"
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return vecs.tolist()
