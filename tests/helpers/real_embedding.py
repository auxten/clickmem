"""Real embedding engine using a local model for semantic distance tests.

Default model: Qwen/Qwen3-Embedding-0.6B (~600M params, 1024d native).
Uses MRL truncation to 256d and prompt_name="query" for query encoding.
The model is downloaded on first use and cached locally.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional


def _get_model(model_name: str = "Qwen/Qwen3-Embedding-0.6B", truncate_dim: Optional[int] = None):
    """Load and cache the sentence-transformer model."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise ImportError(
            "sentence-transformers is required for real embedding tests. "
            "Install with: pip install sentence-transformers"
        )
    kwargs = {}
    if truncate_dim is not None:
        kwargs["truncate_dim"] = truncate_dim
    return SentenceTransformer(model_name, **kwargs)


class RealEmbeddingEngine:
    """Embedding engine backed by a real local model.

    Default model: Qwen/Qwen3-Embedding-0.6B (~600M params, 1024d native)
    - Uses MRL truncation to 256d by default
    - Query encoding uses prompt_name="query" for instruction-tuned retrieval
    - Normalized embeddings
    """

    def __init__(self, model_name: str = "Qwen/Qwen3-Embedding-0.6B", dimension: int = 256):
        self._model_name = model_name
        self._model = None
        self._target_dimension = dimension

    def load(self) -> None:
        self._model = _get_model(self._model_name, truncate_dim=self._target_dimension)

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
        """Encode a batch of strings."""
        assert self._model is not None, "Call load() first"
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return vecs.tolist()
