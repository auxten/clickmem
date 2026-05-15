"""OpenClaw adapter — managed hook + memory/doc import.

OpenClaw discovers user-managed hooks under ``~/.openclaw/hooks/`` and keeps
its bundled session-memory output under ``~/.openclaw/workspace/memory/``.
This adapter installs a small managed hook that lands reset/new/stop events in
ClickMem cold raw storage without adding any LLM work to the OpenClaw loop.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterator, List

from clickmem.adapters.base import RawSession, base_url_default, home, read_text, safe_mtime

name = "openclaw"
label = "OpenClaw"
experimental = False


def _openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(os.path.expanduser(override))
    return home()


def _state_dir() -> Path:
    override = os.environ.get("OPENCLAW_STATE_DIR")
    if override:
        return Path(os.path.expanduser(override))
    return _openclaw_home() / ".openclaw"


def _config_path() -> Path:
    override = os.environ.get("OPENCLAW_CONFIG_PATH")
    if override:
        return Path(os.path.expanduser(override))
    return _state_dir() / "openclaw.json"


def _hook_dir() -> Path:
    return _state_dir() / "hooks" / "clickmem"


def detect() -> bool:
    return bool(os.environ.get("OPENCLAW_SHELL")) or _state_dir().is_dir() or _config_path().is_file()


def iter_raw_sessions(since: float | None = None) -> Iterator[RawSession]:
    memory_dir = _state_dir() / "workspace" / "memory"
    if not memory_dir.is_dir():
        return
    files = [Path(p) for p in glob.glob(str(memory_dir / "*.md"))]
    files.sort(key=safe_mtime, reverse=True)
    for path in files:
        if since is not None and safe_mtime(path) < since:
            continue
        text = read_text(path)
        if len(text.strip()) < 20:
            continue
        yield RawSession(
            session_id=path.stem,
            agent=name,
            text=text,
            meta={"path": str(path), "source": "openclaw-session-memory"},
        )


def iter_doc_paths() -> List[Path]:
    paths: list[Path] = []
    cfg = _config_path()
    if cfg.is_file():
        paths.append(cfg)

    state = _state_dir()
    for fname in ("AGENTS.md", "MEMORY.md", "USER.md", "SOUL.md", "TOOLS.md", "IDENTITY.md"):
        p = state / fname
        if p.is_file():
            paths.append(p)

    memory_dir = state / "workspace" / "memory"
    if memory_dir.is_dir():
        paths.extend(Path(p) for p in glob.glob(str(memory_dir / "*.md")))
    return paths


def _load_config() -> dict[str, Any]:
    cfg = _config_path()
    if not cfg.is_file():
        return {}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_config(data: dict[str, Any]) -> None:
    cfg = _config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_hook(raw_url: str) -> None:
    hook_dir = _hook_dir()
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "HOOK.md").write_text(
        """---
name: clickmem
description: "Land OpenClaw session lifecycle events in ClickMem raw storage"
metadata:
  { "openclaw": { "emoji": "🧠", "events": ["command:new", "command:reset", "command:stop"], "requires": {} } }
---

# ClickMem

Posts OpenClaw command lifecycle context to ClickMem `/v1/raw` as cold raw
storage. Raw transcripts are never recalled automatically.
""",
        encoding="utf-8",
    )
    (hook_dir / "handler.ts").write_text(
        f"""const RAW_URL = "{raw_url}";

const asText = (value: unknown): string => {{
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  try {{
    return JSON.stringify(value, null, 2);
  }} catch {{
    return String(value);
  }}
}};

const handler = async (event: any) => {{
  const action = event?.action || event?.type || "event";
  const context = event?.context || {{}};
  const sessionId =
    event?.sessionKey ||
    context?.sessionKey ||
    context?.sessionEntry?.id ||
    context?.previousSessionEntry?.id ||
    `openclaw-${{Date.now()}}`;
  const text = asText(context?.previousSessionEntry || context?.sessionEntry || context);
  if (!text.trim()) return;

  const url = process.env.CLICKMEM_REMOTE
    ? `${{process.env.CLICKMEM_REMOTE.replace(/\\/$/, "")}}/v1/raw`
    : RAW_URL;
  const headers: Record<string, string> = {{ "content-type": "application/json" }};
  if (process.env.CLICKMEM_API_KEY) {{
    headers.authorization = `Bearer ${{process.env.CLICKMEM_API_KEY}}`;
  }}

  try {{
    await fetch(url, {{
      method: "POST",
      headers,
      body: JSON.stringify({{
        text,
        session_id: String(sessionId),
        agent: "openclaw",
        role: `command:${{action}}`,
        meta: {{ event_type: event?.type || "", action }},
      }}),
    }});
  }} catch {{
    // OpenClaw hooks must not block user command handling.
  }}
}};

export default handler;
""",
        encoding="utf-8",
    )


def install_hooks(server_url: str = "") -> dict[str, Any]:
    base = (server_url or os.environ.get("CLICKMEM_REMOTE") or base_url_default()).rstrip("/")
    raw_url = f"{base}/v1/raw"
    _write_hook(raw_url)

    data = _load_config()
    hooks = data.setdefault("hooks", {})
    internal = hooks.setdefault("internal", {})
    entries = internal.setdefault("entries", {})
    entries["clickmem"] = {"enabled": True}
    _save_config(data)

    return {
        "ok": True,
        "installed": True,
        "agent": name,
        "path": str(_hook_dir()),
        "config": str(_config_path()),
        "raw_url": raw_url,
        "note": "restart the OpenClaw gateway so managed hooks reload",
    }


def uninstall_hooks() -> dict[str, Any]:
    removed: list[str] = []
    hook_dir = _hook_dir()
    if hook_dir.exists():
        shutil.rmtree(hook_dir)
        removed.append(str(hook_dir))

    data = _load_config()
    entries = data.get("hooks", {}).get("internal", {}).get("entries", {})
    if isinstance(entries, dict) and "clickmem" in entries:
        entries.pop("clickmem", None)
        _save_config(data)
        removed.append("hooks.internal.entries.clickmem")

    return {"ok": True, "installed": False, "agent": name, "removed": removed}


def export_blob(dst_path: Path) -> dict[str, Any]:
    items = [{"path": str(p), "mtime": safe_mtime(p)} for p in iter_doc_paths()]
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(json.dumps({"agent": name, "items": items}, indent=2), encoding="utf-8")
    return {"ok": True, "agent": name, "items": len(items), "path": str(dst_path)}
