"""Mock embedding engine using SHA256 hashing for deterministic vectors.

Produces 256-dimensional L2-normalized vectors from text content.
Same text always yields the same vector. Different texts yield different vectors.
"""

from __future__ import annotations

import hashlib
import math
import struct


class MockEmbeddingEngine:
    """Deterministic embedding engine for testing.

    Uses SHA256 hash to generate 256-dimensional float vectors, then L2-normalizes.
    """

    def __init__(self, dimension: int = 256):
        self._dimension = dimension
        self._loaded = False

    def load(self) -> None:
        self._loaded = True

    @property
    def dimension(self) -> int:
        return self._dimension

    def encode_query(self, text: str) -> list[float]:
        """Encode a query string (simulates instruct prefix)."""
        return self._hash_to_vector(f"query:{text}")

    def encode_document(self, text: str) -> list[float]:
        """Encode a document string (no prefix)."""
        return self._hash_to_vector(f"doc:{text}")

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of document strings."""
        return [self.encode_document(t) for t in texts]

    def _hash_to_vector(self, text: str) -> list[float]:
        """Convert text to a deterministic normalized vector.

        Strategy: hash text repeatedly to fill dimension floats,
        then L2-normalize the result.
        """
        dim = self._dimension
        raw = []
        current = text.encode("utf-8")
        while len(raw) < dim:
            h = hashlib.sha256(current).digest()  # 32 bytes = 8 floats
            # Interpret each 4 bytes as a float in [-1, 1]
            for i in range(0, 32, 4):
                if len(raw) >= dim:
                    break
                # Convert 4 bytes to uint32, then scale to [-1, 1]
                val = struct.unpack(">I", h[i : i + 4])[0]
                raw.append((val / 2147483647.5) - 1.0)
            current = h  # chain hash

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in raw))
        if norm < 1e-10:
            return raw
        return [x / norm for x in raw]
