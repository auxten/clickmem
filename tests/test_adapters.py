"""Adapter registry: 10 handles, doc-only adapters surface clean errors,
experimental flag honoured for cline + jetbrains.
"""

from __future__ import annotations

import pytest

from clickmem import adapters


EXPECTED_NAMES = [
    "claude_code",
    "cursor",
    "codex",
    "aider",
    "continue_dev",
    "cline",
    "windsurf",
    "zed",
    "jetbrains",
    "generic",
]


def test_registry_lists_ten_adapters():
    names = [h.name for h in adapters.registry]
    assert names == EXPECTED_NAMES
    assert len(set(names)) == 10


def test_get_returns_handle_for_known_names():
    for name in EXPECTED_NAMES:
        h = adapters.get(name)
        assert h is not None
        assert h.name == name
        assert isinstance(h.label, str) and h.label


def test_experimental_flag_on_cline_and_jetbrains():
    assert adapters.get("cline").experimental is True
    assert adapters.get("jetbrains").experimental is True
    assert adapters.get("claude_code").experimental is False
    assert adapters.get("generic").experimental is False


def test_doc_only_adapter_install_returns_not_implemented_payload():
    """Experimental doc-only adapters return a clean dict (not raise)."""
    out = adapters.get("cline").install_hooks(server_url="http://127.0.0.1:9527")
    assert out["ok"] is False
    assert out["error"] == "doc-only adapter"
    out = adapters.get("jetbrains").install_hooks(server_url="http://127.0.0.1:9527")
    assert out["ok"] is False
    assert out["error"] == "doc-only adapter"


def test_generic_adapter_always_detects():
    assert adapters.get("generic").detect() is True
    res = adapters.get("generic").install_hooks(server_url="http://127.0.0.1:9527")
    assert res["installed"] is True


def test_iter_doc_paths_returns_iterable(monkeypatch, tmp_path):
    for h in adapters.registry:
        paths = h.iter_doc_paths()
        assert isinstance(paths, list)


def test_iter_raw_sessions_safe_for_missing_dirs():
    """Adapters whose home dirs don't exist must still return an empty iterator
    rather than raising."""
    for h in adapters.registry:
        list(h.iter_raw_sessions(since=None))  # must not raise
