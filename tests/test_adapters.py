"""Adapter registry: 12 handles, doc-only adapters surface clean errors,
experimental flag honoured for cline + jetbrains, plus v0 install residue
detection + cleanup for ``clickmem hooks install`` (audit T2.8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clickmem import adapters
from clickmem.adapters import claude_code, codex, cursor, hermes, openclaw
from clickmem.adapters.base import V0ResidueItem, is_v0_hook_entry


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
    "openclaw",
    "hermes",
    "generic",
]


def test_registry_lists_twelve_adapters():
    names = [h.name for h in adapters.registry]
    assert names == EXPECTED_NAMES
    assert len(set(names)) == 12


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


def test_openclaw_install_writes_managed_hook_and_config(monkeypatch, tmp_path):
    state = tmp_path / ".openclaw"
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state))
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)

    res = openclaw.install_hooks(server_url="http://127.0.0.1:9527")
    assert res["ok"] is True
    hook_dir = state / "hooks" / "clickmem"
    assert (hook_dir / "HOOK.md").is_file()
    assert (hook_dir / "handler.ts").is_file()

    config = json.loads((state / "openclaw.json").read_text(encoding="utf-8"))
    assert config["hooks"]["internal"]["entries"]["clickmem"]["enabled"] is True

    out = openclaw.uninstall_hooks()
    assert out["ok"] is True
    assert not hook_dir.exists()
    config = json.loads((state / "openclaw.json").read_text(encoding="utf-8"))
    assert "clickmem" not in config["hooks"]["internal"]["entries"]


def test_hermes_install_writes_gateway_hook(monkeypatch, tmp_path):
    root = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))

    res = hermes.install_hooks(server_url="http://127.0.0.1:9527")
    assert res["ok"] is True
    hook_dir = root / "hooks" / "clickmem"
    assert (hook_dir / "HOOK.yaml").is_file()
    assert (hook_dir / "handler.py").is_file()

    out = hermes.uninstall_hooks()
    assert out["ok"] is True
    assert not hook_dir.exists()


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


def test_clickmem_startup_skill_installer_copies_supported_agent_skill(fake_home):
    from clickmem.skill_install import install_clickmem_skill

    out = install_clickmem_skill("cursor")
    assert out["installed"] is True
    target = Path(out["path"])
    assert target == fake_home / ".cursor" / "skills" / "clickmem" / "SKILL.md"
    text = target.read_text(encoding="utf-8")
    assert "Startup protocol" in text
    assert "timeout_seconds=5.0" in text

    assert install_clickmem_skill("claude_code")["path"] == str(
        fake_home / ".claude" / "skills" / "clickmem" / "SKILL.md"
    )
    assert install_clickmem_skill("codex")["path"] == str(
        fake_home / ".codex" / "skills" / "clickmem" / "SKILL.md"
    )


# ---------- v0 residue cleanup (audit T2.8) -------------------------------


# v0 wrote `enabledPlugins.clickmem@local` plus `UserPromptSubmit` /
# `PostToolUse` curl hooks against the legacy `/hooks/claude-code`
# endpoint. The audit harness pre-seeds exactly this shape and expects
# `clickmem hooks install` to either warn about it or clean it.
_V0_CLAUDE_SETTINGS = {
    "enabledPlugins": {"clickmem@local": True},
    "theme": "dark",
    "hooks": {
        "UserPromptSubmit": [{
            "hooks": [{
                "type": "command",
                "command": "curl -s -X POST http://127.0.0.1:9527/hooks/claude-code -d @-",
            }],
        }],
        "PostToolUse": [{
            "hooks": [{
                "type": "command",
                "command": "curl -s -X POST http://127.0.0.1:9527/hooks/claude-code -d @-",
            }],
        }],
        "Stop": [{
            "hooks": [{"type": "http", "url": "https://example.com/unrelated", "timeout": 5}],
        }],
    },
}

_V0_CODEX_HOOKS = {
    "version": 0,
    "hooks": {
        "on_session_end": [{
            "type": "shell",
            "command": "curl -sS -X POST http://127.0.0.1:9527/hooks/claude-code -d @-",
            "timeout": 15,
        }],
        "on_session_start": [{
            "type": "http",
            "url": "https://example.com/unrelated/start",
        }],
    },
}

_V0_PLUGINS_REGISTRY_LIST = {
    "version": 1,
    "plugins": [
        {"name": "clickmem@local", "version": "0.7.2", "source": "local"},
        {"name": "other-plugin", "version": "1.2.3"},
    ],
}


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    """Repoint claude_code, codex, and cursor adapters at a tmp $HOME tree."""
    home = tmp_path / "home"
    home.mkdir()

    # Claude Code paths
    settings = home / ".claude" / "settings.json"
    plugins_registry = home / ".claude" / "plugins" / "installed_plugins.json"
    clickmem_v0 = home / ".clickmem" / "claude-plugin"
    monkeypatch.setattr(claude_code, "_SETTINGS", settings)
    monkeypatch.setattr(claude_code, "_PLUGINS_REGISTRY", plugins_registry)
    monkeypatch.setattr(claude_code, "_CLICKMEM_V0_PLUGIN_DIR", clickmem_v0)
    monkeypatch.setattr(claude_code, "_BASE", home / ".claude" / "projects")

    # Codex paths
    codex_hooks = home / ".codex" / "hooks.json"
    monkeypatch.setattr(codex, "_BASE", home / ".codex")
    monkeypatch.setattr(codex, "_HOOKS_JSON", codex_hooks)

    # Cursor paths
    legacy = home / ".cursor" / "plugins" / "clickmem"
    hook_dst = home / ".cursor" / "hooks" / "clickmem"
    monkeypatch.setattr(cursor, "_BASE", home / ".cursor" / "projects")
    monkeypatch.setattr(cursor, "_HOOK_DST", hook_dst)
    monkeypatch.setattr(cursor, "_LEGACY_PLUGIN_DST", legacy)

    return home


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _seed_v0_clickmem_plugin_dir(home: Path) -> Path:
    p = home / ".clickmem" / "claude-plugin"
    (p / "hooks").mkdir(parents=True)
    (p / ".claude-plugin").mkdir(parents=True)
    (p / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
    (p / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    return p


def test_is_v0_hook_entry_distinguishes_v0_from_v1():
    """The shared classifier must NOT flag v1 hooks (idempotency contract)."""
    v0_command = {
        "type": "command",
        "command": "curl -s http://127.0.0.1:9527/hooks/claude-code -d @-",
    }
    assert is_v0_hook_entry(v0_command) is True

    v1_recall = {"type": "http", "url": "http://127.0.0.1:9527/v1/recall", "timeout": 5}
    v1_raw = {"type": "http", "url": "http://127.0.0.1:9527/v1/raw", "timeout": 15, "async": True}
    assert is_v0_hook_entry(v1_recall) is False
    assert is_v0_hook_entry(v1_raw) is False

    unrelated = {"type": "http", "url": "https://example.com/whatever"}
    assert is_v0_hook_entry(unrelated) is False


def test_claude_code_detect_v0_residue_finds_every_v0_artefact(fake_home):
    """All four v0 artefact shapes must be surfaced by detect_v0_residue."""
    _write_json(claude_code._SETTINGS, _V0_CLAUDE_SETTINGS)
    _write_json(claude_code._PLUGINS_REGISTRY, _V0_PLUGINS_REGISTRY_LIST)
    _seed_v0_clickmem_plugin_dir(fake_home)

    items = claude_code.detect_v0_residue()
    paths = [item.path for item in items]
    assert str(claude_code._SETTINGS) in paths
    assert str(claude_code._PLUGINS_REGISTRY) in paths
    assert str(claude_code._CLICKMEM_V0_PLUGIN_DIR) in paths

    # The settings file produces TWO findings: enabledPlugins + hook entries.
    settings_findings = [i for i in items if i.path == str(claude_code._SETTINGS)]
    assert len(settings_findings) == 2
    assert {f.action for f in settings_findings} == {"edit-in-place"}


def test_claude_settings_cleanup_drops_enabled_plugin_and_v0_hooks(fake_home):
    """Cleanup must (a) drop ``enabledPlugins.clickmem@local``, (b) strip every
    v0 hook entry, (c) preserve unrelated keys (``theme``) and v1-shaped hooks
    byte-identically to a hand-built control."""
    _write_json(claude_code._SETTINGS, _V0_CLAUDE_SETTINGS)
    items = claude_code.detect_v0_residue()
    log = claude_code.clean_v0_residue(items)
    assert log, "cleanup must produce a non-empty action log"

    expected = {
        "theme": "dark",
        "hooks": {
            "Stop": [{
                "hooks": [{"type": "http", "url": "https://example.com/unrelated", "timeout": 5}],
            }],
        },
    }
    actual = json.loads(claude_code._SETTINGS.read_text(encoding="utf-8"))
    assert actual == expected, f"settings drift: {actual!r}"

    backups = list(claude_code._SETTINGS.parent.glob(f"{claude_code._SETTINGS.name}.bak.*"))
    assert backups, "expected a .bak.<UTC-timestamp> backup beside the original"
    assert backups[0].name.startswith("settings.json.bak.")
    assert json.loads(backups[0].read_text(encoding="utf-8")) == _V0_CLAUDE_SETTINGS


def test_claude_plugins_registry_cleanup_removes_clickmem_at_local(fake_home):
    """The list-shaped registry must drop only ``clickmem@local`` and preserve
    every other plugin entry plus the surrounding ``version`` key."""
    _write_json(claude_code._PLUGINS_REGISTRY, _V0_PLUGINS_REGISTRY_LIST)
    items = claude_code.detect_v0_residue()
    claude_code.clean_v0_residue(items)

    expected = {
        "version": 1,
        "plugins": [
            {"name": "other-plugin", "version": "1.2.3"},
        ],
    }
    actual = json.loads(claude_code._PLUGINS_REGISTRY.read_text(encoding="utf-8"))
    assert actual == expected

    backups = list(claude_code._PLUGINS_REGISTRY.parent.glob(
        f"{claude_code._PLUGINS_REGISTRY.name}.bak.*"
    ))
    assert backups, "registry edit must leave a .bak.<UTC-timestamp> backup"


def test_claude_plugins_registry_handles_dict_shape(fake_home):
    """Some Claude builds wrote ``plugins`` as a dict; cleanup must preserve
    type and drop only the ``clickmem@local`` key."""
    _write_json(claude_code._PLUGINS_REGISTRY, {
        "version": 1,
        "plugins": {
            "clickmem@local": {"version": "0.7.2"},
            "other": {"version": "1.0.0"},
        },
    })
    items = claude_code.detect_v0_residue()
    claude_code.clean_v0_residue(items)

    expected = {"version": 1, "plugins": {"other": {"version": "1.0.0"}}}
    assert json.loads(claude_code._PLUGINS_REGISTRY.read_text(encoding="utf-8")) == expected


def test_clickmem_claude_plugin_dir_removed_when_v0_shape(fake_home):
    """``~/.clickmem/claude-plugin/`` is removed only when it carries the v0
    plugin shape (``hooks/hooks.json`` + ``.claude-plugin/plugin.json``)."""
    plugin_dir = _seed_v0_clickmem_plugin_dir(fake_home)
    assert plugin_dir.is_dir()

    items = claude_code.detect_v0_residue()
    claude_code.clean_v0_residue(items)

    assert not plugin_dir.exists()


def test_clickmem_claude_plugin_dir_kept_when_not_v0_shape(fake_home):
    """A non-plugin ``~/.clickmem/claude-plugin/`` (e.g. user notes) is left
    alone — we never blast a directory we don't recognise."""
    plugin_dir = claude_code._CLICKMEM_V0_PLUGIN_DIR
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "user_notes.md").write_text("hello", encoding="utf-8")

    items = claude_code.detect_v0_residue()
    assert all(item.path != str(plugin_dir) for item in items)
    assert plugin_dir.is_dir()


def test_codex_v0_hooks_stripped_in_place(fake_home):
    """Codex cleanup must strip v0 entries while keeping unrelated entries
    byte-identical."""
    _write_json(codex._HOOKS_JSON, _V0_CODEX_HOOKS)
    items = codex.detect_v0_residue()
    assert items, "codex hook detector must surface the v0 entry"
    codex.clean_v0_residue(items)

    expected = {
        "version": 0,
        "hooks": {
            "on_session_start": [{
                "type": "http",
                "url": "https://example.com/unrelated/start",
            }],
        },
    }
    actual = json.loads(codex._HOOKS_JSON.read_text(encoding="utf-8"))
    assert actual == expected

    backups = list(codex._HOOKS_JSON.parent.glob(f"{codex._HOOKS_JSON.name}.bak.*"))
    assert backups, "codex edit must leave a .bak.<UTC-timestamp> backup"


def test_codex_empty_hooks_json_after_full_strip(fake_home):
    """If every hook entry was v0, ``hooks.json`` collapses to ``{"hooks": {}}``
    — never a missing or malformed file."""
    _write_json(codex._HOOKS_JSON, {
        "version": 0,
        "hooks": {
            "on_session_end": [{
                "type": "shell",
                "command": "curl http://127.0.0.1:9527/hooks/claude-code",
            }],
        },
    })
    items = codex.detect_v0_residue()
    codex.clean_v0_residue(items)

    actual = json.loads(codex._HOOKS_JSON.read_text(encoding="utf-8"))
    assert actual == {"version": 0, "hooks": {}}


def test_cursor_legacy_plugin_dir_removed(fake_home):
    """The ``~/.cursor/plugins/clickmem/`` legacy dir is detected and removed."""
    legacy = cursor._LEGACY_PLUGIN_DST
    legacy.mkdir(parents=True)
    (legacy / "marker").write_text("legacy", encoding="utf-8")

    items = cursor.detect_v0_residue()
    assert items and items[0].action == "rm"
    cursor.clean_v0_residue(items)
    assert not legacy.exists()


def test_cursor_legacy_plugin_symlink_removed(fake_home):
    """A symlinked legacy plugin path is unlinked even if the target is gone."""
    legacy = cursor._LEGACY_PLUGIN_DST
    legacy.parent.mkdir(parents=True)
    target = fake_home / "ghost-target"
    legacy.symlink_to(target)
    assert legacy.is_symlink()

    items = cursor.detect_v0_residue()
    assert items and items[0].detail.get("kind") == "symlink"
    cursor.clean_v0_residue(items)
    assert not legacy.exists() and not legacy.is_symlink()


def test_v0_cleanup_is_idempotent(fake_home):
    """Pass two over the same $HOME must surface zero residue (the contract
    behind the audit's `cleaned_v0` check). v1 hook entries written between
    passes must NOT be flagged."""
    _write_json(claude_code._SETTINGS, _V0_CLAUDE_SETTINGS)
    _write_json(claude_code._PLUGINS_REGISTRY, _V0_PLUGINS_REGISTRY_LIST)
    _write_json(codex._HOOKS_JSON, _V0_CODEX_HOOKS)
    _seed_v0_clickmem_plugin_dir(fake_home)
    legacy = cursor._LEGACY_PLUGIN_DST
    legacy.mkdir(parents=True)

    for adapter in (claude_code, codex, cursor):
        adapter.clean_v0_residue(adapter.detect_v0_residue())

    # Simulate a v1 install writing v1 hooks back into both files.
    _write_json(claude_code._SETTINGS, {
        "hooks": {
            "SessionStart": [{
                "hooks": [{"type": "http", "url": "http://127.0.0.1:9527/v1/recall", "timeout": 5}],
            }],
            "Stop": [{
                "hooks": [{"type": "http", "url": "http://127.0.0.1:9527/v1/raw", "timeout": 15, "async": True}],
            }],
        },
    })
    _write_json(codex._HOOKS_JSON, {
        "hooks": {
            "on_session_start": [{"type": "http", "url": "http://127.0.0.1:9527/v1/recall", "timeout": 5}],
            "on_session_end": [{"type": "http", "url": "http://127.0.0.1:9527/v1/raw", "timeout": 15, "async": True}],
        },
    })

    for adapter in (claude_code, codex, cursor):
        assert adapter.detect_v0_residue() == [], (
            f"second pass surfaced residue for {adapter.name}: "
            f"{adapter.detect_v0_residue()!r}"
        )


def test_install_hooks_for_all_returns_v0_residue_payload(monkeypatch, fake_home):
    """End-to-end: ``install_hooks_for_all`` must surface a stable
    ``v0_residue`` block that the dashboard can render. Detection runs
    regardless of cleanup, and cleanup is on by default."""
    _write_json(claude_code._SETTINGS, _V0_CLAUDE_SETTINGS)
    _write_json(claude_code._PLUGINS_REGISTRY, _V0_PLUGINS_REGISTRY_LIST)
    _write_json(codex._HOOKS_JSON, _V0_CODEX_HOOKS)
    legacy = cursor._LEGACY_PLUGIN_DST
    legacy.mkdir(parents=True)

    # No-op the actual install_hooks calls so we don't need a fake cursor-hooks
    # source tree (covered separately in the cursor install test above).
    for h in adapters.registry:
        monkeypatch.setattr(
            h.module, "install_hooks",
            lambda url="", _name=h.name: {"ok": True, "installed": True, "agent": _name},
        )
    # event_write would otherwise touch the chDB backend; route to a no-op.
    from clickmem import hooks_install
    monkeypatch.setattr(hooks_install, "event_write", lambda *a, **kw: None)
    monkeypatch.setattr(hooks_install, "_server_url", lambda override=None: "http://127.0.0.1:9527")

    out = hooks_install.install_hooks_for_all(clean_v0_residue=True)
    assert out["ok"] is True
    block = out["v0_residue"]
    assert block["skipped_reason"] == ""
    assert len(block["detected"]) >= 4  # claude settings (x2), codex, cursor, plugins registry
    assert block["cleaned"], "cleaned[] must be populated when clean_v0_residue=True"
    detected_paths = {d["path"] for d in block["detected"]}
    assert str(claude_code._SETTINGS) in detected_paths
    assert str(claude_code._PLUGINS_REGISTRY) in detected_paths
    assert str(codex._HOOKS_JSON) in detected_paths
    assert str(legacy) in detected_paths

    # Idempotency contract: a second run after cleanup surfaces zero residue.
    out2 = hooks_install.install_hooks_for_all(clean_v0_residue=True)
    assert out2["v0_residue"]["detected"] == []
    assert out2["v0_residue"]["cleaned"] == []


def test_install_hooks_for_all_keep_v0_skips_cleanup(monkeypatch, fake_home):
    """``clean_v0_residue=False`` (the post-merge ``--keep-v0`` flag) still
    runs detection and reports findings, but applies no edits."""
    _write_json(claude_code._SETTINGS, _V0_CLAUDE_SETTINGS)
    legacy = cursor._LEGACY_PLUGIN_DST
    legacy.mkdir(parents=True)

    for h in adapters.registry:
        monkeypatch.setattr(
            h.module, "install_hooks",
            lambda url="", _name=h.name: {"ok": True, "installed": True, "agent": _name},
        )
    from clickmem import hooks_install
    monkeypatch.setattr(hooks_install, "event_write", lambda *a, **kw: None)
    monkeypatch.setattr(hooks_install, "_server_url", lambda override=None: "http://127.0.0.1:9527")

    out = hooks_install.install_hooks_for_all(clean_v0_residue=False)
    block = out["v0_residue"]
    assert block["skipped_reason"] == "user requested --keep-v0"
    assert block["detected"], "detection must still run when cleanup is skipped"
    assert block["cleaned"] == []

    # Verify nothing was actually edited / removed on disk.
    assert legacy.is_dir()
    settings = json.loads(claude_code._SETTINGS.read_text(encoding="utf-8"))
    assert settings.get("enabledPlugins", {}).get("clickmem@local") is True


def test_v0_residue_item_to_dict_round_trips():
    """The dataclass must serialise to a stable shape for the dashboard."""
    item = V0ResidueItem(
        adapter="claude_code",
        path="/tmp/x",
        issue="things",
        action="edit-in-place",
        detail={"k": 1},
    )
    d = item.to_dict()
    assert d == {
        "adapter": "claude_code",
        "path": "/tmp/x",
        "issue": "things",
        "action": "edit-in-place",
        "detail": {"k": 1},
    }
