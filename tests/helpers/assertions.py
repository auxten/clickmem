"""Custom assertion helpers for memory tests.

Provides domain-specific assertions that produce clear failure messages.
"""

from __future__ import annotations

import re
from typing import Any

from memory_core.models import Memory

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def assert_valid_uuid(value: str) -> None:
    """Assert that value is a valid UUID4 string."""
    assert isinstance(value, str), f"Expected str, got {type(value)}"
    assert UUID_PATTERN.match(value), f"Not a valid UUID: {value!r}"


def assert_memory_fields(memory: Memory, **expected: Any) -> None:
    """Assert that a Memory object has the expected field values.

    Example:
        assert_memory_fields(m, layer="semantic", category="preference")
    """
    for field_name, expected_val in expected.items():
        actual = getattr(memory, field_name, _SENTINEL)
        assert actual is not _SENTINEL, f"Memory has no field {field_name!r}"
        assert actual == expected_val, (
            f"Memory.{field_name}: expected {expected_val!r}, got {actual!r}"
        )


def assert_layer_count(memories: list[Memory], layer: str, expected_count: int) -> None:
    """Assert the number of memories in a given layer."""
    actual = sum(1 for m in memories if m.layer == layer)
    assert actual == expected_count, (
        f"Expected {expected_count} memories in layer {layer!r}, got {actual}"
    )


def assert_search_results_ordered_by_score(results: list[dict]) -> None:
    """Assert that search results are sorted by final_score descending."""
    scores = [r["final_score"] for r in results]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], (
            f"Results not ordered by score: {scores[i]} < {scores[i+1]} at index {i}"
        )


def assert_no_duplicate_ids(items: list) -> None:
    """Assert no duplicate IDs in a list of Memory objects or dicts."""
    ids = []
    for item in items:
        if isinstance(item, dict):
            ids.append(item.get("id"))
        else:
            ids.append(getattr(item, "id", None))
    seen = set()
    for mid in ids:
        assert mid not in seen, f"Duplicate ID found: {mid}"
        seen.add(mid)


def assert_all_layer(memories: list[Memory], layer: str) -> None:
    """Assert that all memories belong to the specified layer."""
    for m in memories:
        assert m.layer == layer, (
            f"Expected layer {layer!r}, got {m.layer!r} for memory {m.id}"
        )


def assert_memory_active(memory: Memory) -> None:
    """Assert the memory is active."""
    assert memory.is_active is True, f"Memory {memory.id} is not active"


def assert_memory_inactive(memory: Memory) -> None:
    """Assert the memory is inactive (soft-deleted)."""
    assert memory.is_active is False, f"Memory {memory.id} is still active"


class _SentinelType:
    """Sentinel for missing attributes."""

    def __repr__(self) -> str:
        return "<MISSING>"


_SENTINEL = _SentinelType()
