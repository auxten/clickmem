"""Simple Bearer-token authentication for ClickMem server."""

from __future__ import annotations

import os
import secrets


def generate_api_key() -> str:
    """Generate a random 32-char hex API key."""
    return secrets.token_hex(16)


def verify_api_key(provided: str | None, expected: str | None = None) -> bool:
    """Return True if authentication passes.

    If no expected key is configured (empty / None), all requests pass.
    """
    expected = expected or os.environ.get("CLICKMEM_API_KEY", "")
    if not expected:
        return True
    if not provided:
        return False
    return secrets.compare_digest(provided, expected)
