"""Claude Code adapter — reads ``~/.claude/projects/*/{*.jsonl, memory/*.md}``."""

from __future__ import annotations

import glob
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import (
    RawSession,
    V0ResidueItem,
    backup_file,
    base_url_default,
    home,
    is_v0_hook_entry,
    iter_jsonl,
    remove_path,
    safe_mtime,
    safe_remove,
)

_log = logging.getLogger(__name__)

name = "claude_code"
label = "Claude Code"
experimental = False

_BASE = home() / ".claude" / "projects"
_SETTINGS = home() / ".claude" / "settings.json"
_PLUGINS_REGISTRY = home() / ".claude" / "plugins" / "installed_plugins.json"
_CLICKMEM_V0_PLUGIN_DIR = home() / ".clickmem" / "claude-plugin"
_LEGACY_HOOK_KEYS = ("UserPromptSubmit", "PostToolUse", "SessionEnd")
_SKIP_TYPES = frozenset({"queue-operation", "file-history-snapshot", "progress", "last-prompt", "summary"})


def detect() -> bool:
    return _BASE.is_dir() or (home() / ".claude").is_dir()


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", "") or "")
            elif block.get("type") == "tool_result":
                inner = block.get("content", "")
                if isinstance(inner, str):
                    parts.append(inner)
        return "\n".join(p for p in parts if p).strip()
    return ""


def _parse_session(path: Path) -> RawSession | None:
    messages: list[str] = []
    cwd = ""
    for obj in iter_jsonl(path):
        if obj.get("type", "") in _SKIP_TYPES:
            continue
        if not cwd:
            cwd = obj.get("cwd", "") or ""
        msg = obj.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        text = _extract_text(msg.get("content", ""))
        if not text:
            continue
        if role in ("user", "assistant"):
            messages.append(f"{role}: {text}")
    if not messages:
        return None
    return RawSession(
        session_id=path.stem,
        agent=name,
        text="\n".join(messages),
        cwd=cwd,
        meta={"path": str(path)},
    )


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    if not _BASE.is_dir():
        return
    files = [Path(p) for p in glob.glob(str(_BASE / "*" / "*.jsonl"))]
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        info = _parse_session(path)
        if info and len(info.text) >= 50:
            yield info


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    if _BASE.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(_BASE / "*" / "memory" / "*.md")))
    return paths


def _load_settings() -> dict[str, Any]:
    if not _SETTINGS.is_file():
        return {}
    try:
        return json.loads(_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict[str, Any]) -> None:
    _SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def install_hooks(server_url: str = "") -> dict[str, Any]:
    """Write ``~/.claude/settings.json`` with only ``SessionStart`` + ``Stop`` HTTP hooks."""
    base = (server_url or os.environ.get("CLICKMEM_REMOTE") or base_url_default()).rstrip("/")
    data = _load_settings()
    hooks = data.setdefault("hooks", {})

    recall_url = f"{base}/v1/recall"
    raw_url = f"{base}/v1/raw"

    hooks["SessionStart"] = [{
        "hooks": [{"type": "http", "url": recall_url, "timeout": 5}],
    }]
    hooks["Stop"] = [{
        "hooks": [{"type": "http", "url": raw_url, "timeout": 15, "async": True}],
    }]
    for legacy in ("UserPromptSubmit", "PostToolUse", "SessionEnd"):
        hooks.pop(legacy, None)

    _save_settings(data)
    return {"ok": True, "installed": True, "agent": name, "settings": str(_SETTINGS), "recall_url": recall_url, "raw_url": raw_url}


def uninstall_hooks() -> dict[str, Any]:
    data = _load_settings()
    hooks = data.get("hooks", {}) or {}
    removed = []
    for key in ("SessionStart", "Stop", "UserPromptSubmit", "PostToolUse", "SessionEnd"):
        if key in hooks:
            hooks.pop(key, None)
            removed.append(key)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _save_settings(data)
    return {"ok": True, "installed": False, "agent": name, "removed": removed}


def export_blob(dst_path: Path) -> dict[str, Any]:
    """Copy a thin manifest of session paths into ``dst_path`` (one JSON file)."""
    items = []
    for p in (Path(x) for x in glob.glob(str(_BASE / "*" / "*.jsonl"))):
        items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}


# ---------- v0 residue cleanup -------------------------------------------


def _settings_v0_hook_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk ``hooks.<key>[].hooks[]`` and return descriptors for v0 entries."""
    findings: list[dict[str, Any]] = []
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return findings
    for key, blocks in hooks.items():
        if not isinstance(blocks, list):
            continue
        for bidx, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            inner = block.get("hooks", [])
            if not isinstance(inner, list):
                continue
            for hidx, entry in enumerate(inner):
                if is_v0_hook_entry(entry):
                    findings.append({
                        "hook_key": key,
                        "block_idx": bidx,
                        "hook_idx": hidx,
                        "entry": entry,
                    })
    return findings


def _is_v0_clickmem_plugin_dir(p: Path) -> bool:
    """``~/.clickmem/claude-plugin/`` is the v0 install shape."""
    if not p.is_dir():
        return False
    return (p / "hooks" / "hooks.json").is_file() and (p / ".claude-plugin" / "plugin.json").is_file()


def detect_v0_residue() -> list[V0ResidueItem]:
    """Surface every pre-v1 Claude Code artefact under ``$HOME``.

    Detection rules:

    * ``~/.claude/settings.json`` carries an ``enabledPlugins.clickmem@local``
      key (v0 used Claude's plugin enable bit; v1 ships hooks only).
    * ``~/.claude/settings.json`` has any hook entry that ``is_v0_hook_entry``
      flags — i.e. references the legacy ``/hooks/claude-code`` endpoint or
      uses a non-``http`` ``command`` style entry mentioning ``clickmem`` /
      ``9527``. v1 hooks (``type: http`` against ``/v1/recall`` / ``/v1/raw``)
      are explicitly NOT flagged so the cleaner is idempotent after install.
    * ``~/.claude/plugins/installed_plugins.json`` lists ``clickmem@local`` —
      Claude's plugin registry needs the entry removed.
    * ``~/.clickmem/claude-plugin/`` is a v0 install tree (``hooks/hooks.json``
      + ``.claude-plugin/plugin.json``); v1 ships no such directory.
    """
    items: list[V0ResidueItem] = []

    if _SETTINGS.is_file():
        data = _load_settings()
        enabled = data.get("enabledPlugins", {})
        if isinstance(enabled, dict) and "clickmem@local" in enabled:
            items.append(V0ResidueItem(
                adapter=name,
                path=str(_SETTINGS),
                issue="enabledPlugins.clickmem@local present (v0 plugin flag)",
                action="edit-in-place",
                detail={"key": "enabledPlugins.clickmem@local"},
            ))
        v0_hooks = _settings_v0_hook_findings(data)
        if v0_hooks:
            items.append(V0ResidueItem(
                adapter=name,
                path=str(_SETTINGS),
                issue=f"{len(v0_hooks)} v0 hook entries reference clickmem/9527",
                action="edit-in-place",
                detail={"hook_findings": v0_hooks},
            ))

    if _PLUGINS_REGISTRY.is_file():
        plugins_data = _load_plugins_registry()
        if _plugins_registry_has_clickmem(plugins_data):
            items.append(V0ResidueItem(
                adapter=name,
                path=str(_PLUGINS_REGISTRY),
                issue="installed_plugins.json registers clickmem@local (v0)",
                action="edit-in-place",
                detail={"plugin_name": "clickmem@local"},
            ))

    if _is_v0_clickmem_plugin_dir(_CLICKMEM_V0_PLUGIN_DIR):
        items.append(V0ResidueItem(
            adapter=name,
            path=str(_CLICKMEM_V0_PLUGIN_DIR),
            issue="~/.clickmem/claude-plugin/ is a leftover v0 install tree",
            action="rm",
            detail={"shape": "claude-plugin"},
        ))

    return items


def _load_plugins_registry() -> Any:
    if not _PLUGINS_REGISTRY.is_file():
        return None
    try:
        return json.loads(_PLUGINS_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_plugins_registry(data: Any) -> None:
    _PLUGINS_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    _PLUGINS_REGISTRY.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _plugins_registry_has_clickmem(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    plugins = data.get("plugins")
    if isinstance(plugins, list):
        return any(isinstance(p, dict) and p.get("name") == "clickmem@local" for p in plugins)
    if isinstance(plugins, dict):
        return "clickmem@local" in plugins
    return False


def _drop_clickmem_from_plugins_registry(data: Any) -> Any:
    """Remove ``clickmem@local`` from the registry, preserving container type."""
    if not isinstance(data, dict):
        return data
    plugins = data.get("plugins")
    if isinstance(plugins, list):
        data["plugins"] = [p for p in plugins if not (isinstance(p, dict) and p.get("name") == "clickmem@local")]
    elif isinstance(plugins, dict):
        plugins.pop("clickmem@local", None)
        data["plugins"] = plugins
    return data


def _strip_v0_hooks_from_settings(data: dict[str, Any]) -> int:
    """Remove every v0 entry from ``hooks.<key>[].hooks[]`` in-place.

    Empty inner ``hooks`` arrays drop their parent block; empty hook keys
    drop the key entirely; a hooks-less ``hooks`` value collapses to ``{}``.
    Returns the number of v0 entries removed.
    """
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for key in list(hooks.keys()):
        blocks = hooks.get(key)
        if not isinstance(blocks, list):
            continue
        new_blocks: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue
            inner = block.get("hooks", [])
            if not isinstance(inner, list):
                new_blocks.append(block)
                continue
            kept = [e for e in inner if not is_v0_hook_entry(e)]
            removed += len(inner) - len(kept)
            if kept:
                new_block = dict(block)
                new_block["hooks"] = kept
                new_blocks.append(new_block)
        if new_blocks:
            hooks[key] = new_blocks
        else:
            hooks.pop(key, None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    return removed


def clean_v0_residue(items: list[V0ResidueItem]) -> list[dict[str, Any]]:
    """Apply each finding's prescribed action. Returns a per-item action log.

    Files are backed up to a sibling ``.bak.<UTC-YYYYMMDDHHMMSS>`` before
    being rewritten; directories are backed up before removal. The cleaner
    never nukes a file wholesale — it surgically removes only the v0 keys /
    entries / plugin records that ``detect_v0_residue`` flagged.
    """
    log: list[dict[str, Any]] = []
    settings_dirty = False
    settings_data: dict[str, Any] | None = None
    settings_actions: list[str] = []
    settings_backup: Path | None = None

    for item in items:
        try:
            if item.adapter != name:
                continue
            if str(item.path) == str(_SETTINGS):
                if settings_data is None:
                    settings_data = _load_settings()
                    settings_backup = backup_file(_SETTINGS)
                if "enabledPlugins" in str(item.detail.get("key", "")):
                    enabled = settings_data.get("enabledPlugins", {})
                    if isinstance(enabled, dict) and enabled.pop("clickmem@local", None) is not None:
                        if not enabled:
                            settings_data.pop("enabledPlugins", None)
                        else:
                            settings_data["enabledPlugins"] = enabled
                        settings_actions.append("dropped enabledPlugins.clickmem@local")
                        settings_dirty = True
                else:
                    n = _strip_v0_hooks_from_settings(settings_data)
                    if n:
                        settings_actions.append(f"stripped {n} v0 hook entries")
                        settings_dirty = True
                continue
            if str(item.path) == str(_PLUGINS_REGISTRY):
                backup = backup_file(_PLUGINS_REGISTRY)
                data = _load_plugins_registry()
                if isinstance(data, dict):
                    _drop_clickmem_from_plugins_registry(data)
                    _save_plugins_registry(data)
                log.append({
                    "adapter": name,
                    "path": str(_PLUGINS_REGISTRY),
                    "action": "edit-in-place",
                    "detail": "removed clickmem@local from installed_plugins.json",
                    "backup": str(backup) if backup else None,
                })
                continue
            if str(item.path) == str(_CLICKMEM_V0_PLUGIN_DIR):
                backup = backup_file(_CLICKMEM_V0_PLUGIN_DIR)
                existed, err = remove_path(_CLICKMEM_V0_PLUGIN_DIR)
                log.append({
                    "adapter": name,
                    "path": str(_CLICKMEM_V0_PLUGIN_DIR),
                    "action": "rm",
                    "detail": f"removed v0 claude-plugin tree (existed={existed})",
                    "backup": str(backup) if backup else None,
                    "error": err,
                })
                continue
        except OSError as e:
            log.append({
                "adapter": name,
                "path": item.path,
                "action": item.action,
                "error": str(e),
            })

    if settings_dirty and settings_data is not None:
        _save_settings(settings_data)
        log.append({
            "adapter": name,
            "path": str(_SETTINGS),
            "action": "edit-in-place",
            "detail": "; ".join(settings_actions),
            "backup": str(settings_backup) if settings_backup else None,
        })

    return log
