"""Adapter registry: 10 handles, doc-only adapters surface clean errors,
experimental flag honoured for cline + jetbrains.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clickmem import adapters
from clickmem.adapters import cursor


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


def _fake_cursor_hooks_src(root: Path) -> Path:
    """Build a minimal ``cursor-hooks/`` source tree under ``root``."""
    src = root / "cursor-hooks"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
    (src / "hooks" / "stop.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return src


def test_cursor_install_writes_to_hooks_and_uninstall_cleans_both(monkeypatch, tmp_path):
    """install_hooks must land in ~/.cursor/hooks/clickmem; uninstall_hooks
    must clean both the modern path AND a legacy ~/.cursor/plugins/clickmem.
    """
    hook_dst = tmp_path / ".cursor" / "hooks" / "clickmem"
    legacy_dst = tmp_path / ".cursor" / "plugins" / "clickmem"
    monkeypatch.setattr(cursor, "_HOOK_DST", hook_dst)
    monkeypatch.setattr(cursor, "_LEGACY_PLUGIN_DST", legacy_dst)

    src = _fake_cursor_hooks_src(tmp_path / "repo")
    monkeypatch.setattr(cursor, "_repo_cursor_hooks_dir", lambda: src)

    res = cursor.install_hooks(server_url="http://127.0.0.1:9527")
    assert res["ok"] is True
    assert res["installed"] is True
    assert res["path"] == str(hook_dst)
    assert hook_dst.is_dir()
    assert (hook_dst / "hooks" / "hooks.json").is_file()
    env_file = hook_dst / "hooks" / ".env"
    assert env_file.is_file()
    assert "CLICKMEM_REMOTE=http://127.0.0.1:9527" in env_file.read_text(encoding="utf-8")

    legacy_dst.mkdir(parents=True)
    (legacy_dst / "marker").write_text("legacy", encoding="utf-8")

    res = cursor.uninstall_hooks()
    assert res["ok"] is True
    assert res["installed"] is False
    assert not hook_dst.exists()
    assert not legacy_dst.exists()
    assert str(hook_dst) in res["removed"]
    assert str(legacy_dst) in res["removed"]


def test_cursor_uninstall_idempotent_when_nothing_installed(monkeypatch, tmp_path):
    hook_dst = tmp_path / ".cursor" / "hooks" / "clickmem"
    legacy_dst = tmp_path / ".cursor" / "plugins" / "clickmem"
    monkeypatch.setattr(cursor, "_HOOK_DST", hook_dst)
    monkeypatch.setattr(cursor, "_LEGACY_PLUGIN_DST", legacy_dst)

    res = cursor.uninstall_hooks()
    assert res["ok"] is True
    assert res["installed"] is False
    assert res.get("message") == "no hook directory"


def test_cursor_detect_recognises_legacy_install(monkeypatch, tmp_path):
    """detect() should be True when only the legacy plugin path exists."""
    fake_home = tmp_path / "home"
    monkeypatch.setattr(cursor, "_BASE", fake_home / ".cursor" / "projects")
    hook_dst = fake_home / ".cursor" / "hooks" / "clickmem"
    legacy_dst = fake_home / ".cursor" / "plugins" / "clickmem"
    monkeypatch.setattr(cursor, "_HOOK_DST", hook_dst)
    monkeypatch.setattr(cursor, "_LEGACY_PLUGIN_DST", legacy_dst)
    monkeypatch.setattr(cursor, "home", lambda: fake_home)

    assert cursor.detect() is False
    legacy_dst.mkdir(parents=True)
    assert cursor.detect() is True
