"""Codex CLI adapter — reads ``~/.codex/sessions/**/rollout-*.jsonl``."""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import sys
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
    safe_mtime,
)

_log = logging.getLogger(__name__)

name = "codex"
label = "Codex CLI"
experimental = False

_BASE = home() / ".codex"
_HOOKS_JSON = _BASE / "hooks.json"
_CLICKMEM_V1_ENDPOINTS = ("/v1/recall", "/v1/raw")


def detect() -> bool:
    return _BASE.is_dir()


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text", "") or ""
            if not text:
                continue
            if text.startswith("<permissions ") or text.startswith("<app-context>"):
                continue
            if text.startswith("<collaboration_mode>") or text.startswith("<skills_instructions>"):
                continue
            if text.startswith("<environment_context>"):
                continue
            parts.append(text)
        return "\n".join(p for p in parts if p).strip()
    return ""


def _parse_session(path: Path) -> RawSession | None:
    messages: list[str] = []
    cwd = ""
    for obj in iter_jsonl(path):
        rec_type = obj.get("type", "")
        payload = obj.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if rec_type in ("session_meta", "turn_context") and not cwd:
            cwd = payload.get("cwd", "") or ""
            continue
        if rec_type == "response_item" and payload.get("type") == "message":
            role = payload.get("role", "")
            if role not in ("user", "assistant"):
                continue
            text = _extract_text(payload.get("content", []))
            if text:
                messages.append(f"{role}: {text}")
        elif rec_type == "event_msg" and payload.get("type") == "user_message":
            msg = payload.get("message", "")
            if msg and len(msg) > 5:
                messages.append(f"user: {msg}")
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
    sessions_dir = _BASE / "sessions"
    if not sessions_dir.is_dir():
        return
    files: list[Path] = []
    for root, _dirs, names in os.walk(sessions_dir):
        for fname in names:
            if fname.endswith(".jsonl") and fname.startswith("rollout-"):
                files.append(Path(root) / fname)
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        info = _parse_session(path)
        if info and len(info.text) >= 50:
            yield info


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    if (_BASE / "AGENTS.md").is_file():
        paths.append(_BASE / "AGENTS.md")
    memories = _BASE / "memories"
    if memories.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(memories / "*.md")))
    return paths


def _load_hooks() -> dict[str, Any]:
    if not _HOOKS_JSON.is_file():
        return {}
    try:
        return json.loads(_HOOKS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_hooks(data: dict[str, Any]) -> None:
    _HOOKS_JSON.parent.mkdir(parents=True, exist_ok=True)
    _HOOKS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _config_toml() -> Path:
    return _BASE / "config.toml"


def _is_clickmem_v1_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    url = str(entry.get("url", ""))
    return any(endpoint in url for endpoint in _CLICKMEM_V1_ENDPOINTS)


def _strip_clickmem_v1_hooks(hooks: dict[str, Any]) -> int:
    """Remove ClickMem v1 hooks from both legacy and Codex Desktop schemas."""
    removed = 0
    for key in ("on_session_start", "on_session_end", "SessionStart", "Stop"):
        entries = hooks.get(key)
        if not isinstance(entries, list):
            continue
        kept: list[Any] = []
        for entry in entries:
            if _is_clickmem_v1_hook(entry):
                removed += 1
                continue
            if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                nested = [h for h in entry["hooks"] if not _is_clickmem_v1_hook(h)]
                removed += len(entry["hooks"]) - len(nested)
                if nested:
                    new_entry = dict(entry)
                    new_entry["hooks"] = nested
                    kept.append(new_entry)
                continue
            kept.append(entry)
        if kept:
            hooks[key] = kept
        else:
            hooks.pop(key, None)
    return removed


def _clickmem_mcp_command() -> str:
    path = shutil.which("clickmem-mcp")
    if path:
        return str(Path(path).resolve())
    return str(Path(sys.executable).with_name("clickmem-mcp"))


def _toml_quote(value: str) -> str:
    return json.dumps(value)


def _strip_toml_sections(text: str, section: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            header = stripped.strip("[]").strip()
            skipping = header == section or header.startswith(f"{section}.")
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip()


def _install_mcp_config(server_url: str) -> dict[str, Any]:
    path = _config_toml()
    command = _clickmem_mcp_command()
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    kept = _strip_toml_sections(existing, "mcp_servers.clickmem")
    block = (
        "[mcp_servers.clickmem]\n"
        f"command = {_toml_quote(command)}\n"
        "args = []\n\n"
        "[mcp_servers.clickmem.env]\n"
        f"CLICKMEM_REMOTE = {_toml_quote(server_url)}\n"
    )
    body = f"{kept}\n\n{block}" if kept else block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path), "command": command, "remote": server_url}


def _uninstall_mcp_config() -> dict[str, Any]:
    path = _config_toml()
    if not path.is_file():
        return {"ok": True, "path": str(path), "removed": False}
    existing = path.read_text(encoding="utf-8")
    kept = _strip_toml_sections(existing, "mcp_servers.clickmem")
    path.write_text(kept.rstrip() + ("\n" if kept else ""), encoding="utf-8")
    return {"ok": True, "path": str(path), "removed": kept != existing.rstrip()}


def install_hooks(server_url: str = "") -> dict[str, Any]:
    """Write Codex Desktop hooks and stdio MCP config for ClickMem."""
    base = (server_url or os.environ.get("CLICKMEM_REMOTE") or base_url_default()).rstrip("/")
    data = _load_hooks()
    hooks = data.setdefault("hooks", {})

    raw_url = f"{base}/v1/raw"
    recall_url = f"{base}/v1/recall"

    _strip_clickmem_v1_hooks(hooks)
    hooks.setdefault("SessionStart", []).append({
        "hooks": [{"type": "http", "url": recall_url, "timeout": 5}],
    })
    hooks.setdefault("Stop", []).append({
        "hooks": [{"type": "http", "url": raw_url, "timeout": 15, "async": True}],
    })

    _save_hooks(data)
    mcp_config = _install_mcp_config(base)
    return {
        "ok": True,
        "installed": True,
        "agent": name,
        "path": str(_HOOKS_JSON),
        "raw_url": raw_url,
        "recall_url": recall_url,
        "mcp_config": mcp_config,
    }


def uninstall_hooks() -> dict[str, Any]:
    data = _load_hooks()
    removed: list[str] = []
    hooks = data.get("hooks", {}) or {}
    removed_count = _strip_clickmem_v1_hooks(hooks)
    if removed_count:
        removed.append(f"{removed_count} clickmem hook entries")
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _save_hooks(data)
    return {"ok": True, "installed": False, "agent": name, "removed": removed, "mcp_config": _uninstall_mcp_config()}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    sessions_dir = _BASE / "sessions"
    if sessions_dir.is_dir():
        for root, _dirs, names in os.walk(sessions_dir):
            for fname in names:
                if fname.endswith(".jsonl") and fname.startswith("rollout-"):
                    p = Path(root) / fname
                    items.append({"path": str(p), "mtime": safe_mtime(p)})
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}


# ---------- v0 residue cleanup -------------------------------------------


def _hooks_v0_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk ``hooks.<key>[]`` looking for v0 entries (curl shell calls)."""
    findings: list[dict[str, Any]] = []
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return findings
    for key, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        for idx, entry in enumerate(entries):
            if is_v0_hook_entry(entry):
                findings.append({"hook_key": key, "hook_idx": idx, "entry": entry})
    return findings


def detect_v0_residue() -> list[V0ResidueItem]:
    """Surface every pre-v1 Codex CLI hook artefact under ``$HOME``.

    v0 wrote ``~/.codex/hooks.json`` with ``type: shell`` curl commands
    pointing at the legacy ``/hooks/claude-code`` endpoint. v1 writes
    ``type: http`` entries against ``/v1/recall`` and ``/v1/raw`` — those
    are explicitly NOT flagged here so the cleaner is idempotent after
    install.
    """
    items: list[V0ResidueItem] = []
    if not _HOOKS_JSON.is_file():
        return items
    data = _load_hooks()
    findings = _hooks_v0_findings(data)
    if findings:
        items.append(V0ResidueItem(
            adapter=name,
            path=str(_HOOKS_JSON),
            issue=f"{len(findings)} v0 hook entries reference clickmem/9527",
            action="edit-in-place",
            detail={"hook_findings": findings},
        ))
    return items


def _strip_v0_hooks_from_codex(data: dict[str, Any]) -> int:
    """Drop every v0 entry from ``hooks.<key>[]`` in-place."""
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for key in list(hooks.keys()):
        entries = hooks.get(key)
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not is_v0_hook_entry(e)]
        removed += len(entries) - len(kept)
        if kept:
            hooks[key] = kept
        else:
            hooks.pop(key, None)
    if hooks:
        data["hooks"] = hooks
    else:
        data["hooks"] = {}
    return removed


def clean_v0_residue(items: list[V0ResidueItem]) -> list[dict[str, Any]]:
    """Strip v0 hook entries from ``~/.codex/hooks.json`` in-place."""
    log: list[dict[str, Any]] = []
    for item in items:
        if item.adapter != name:
            continue
        if str(item.path) != str(_HOOKS_JSON):
            continue
        try:
            backup = backup_file(_HOOKS_JSON)
            data = _load_hooks()
            n = _strip_v0_hooks_from_codex(data)
            _save_hooks(data)
            log.append({
                "adapter": name,
                "path": str(_HOOKS_JSON),
                "action": "edit-in-place",
                "detail": f"stripped {n} v0 hook entries from codex hooks.json",
                "backup": str(backup) if backup else None,
            })
        except OSError as e:
            log.append({
                "adapter": name,
                "path": str(_HOOKS_JSON),
                "action": "edit-in-place",
                "error": str(e),
            })
    return log
