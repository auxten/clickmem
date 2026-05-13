"""Orchestrate per-adapter ``install_hooks(server_url)`` calls.

Used by ``clickmem hooks install [--agent NAME]``. When ``--agent`` is set
we drive that single adapter; otherwise we walk every discovered adapter and
call ``install_hooks`` on each one, capturing per-adapter results.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from clickmem.adapters import registry
from clickmem.config import get_config
from clickmem.events import write as event_write


_log = logging.getLogger(__name__)


def _server_url(override: str | None = None) -> str:
    if override:
        return override.rstrip("/")
    cfg = get_config(refresh=True)
    return (cfg.remote_url or cfg.server_url()).rstrip("/")


def install(agent: Optional[str] = None, server_url: str | None = None) -> dict[str, Any]:
    url = _server_url(server_url)
    results: List[dict[str, Any]] = []
    target = registry if not agent else [h for h in registry if h.name == agent]
    if agent and not target:
        return {"ok": False, "error": f"unknown adapter: {agent}", "server_url": url}

    for h in target:
        if not agent and not h.detect():
            results.append({"agent": h.name, "skipped": "not discovered"})
            continue
        result = h.install_hooks(url)
        result.setdefault("agent", h.name)
        results.append(result)
        event_write(
            "agent.install",
            agent=h.name,
            message=result.get("error") or result.get("message", "hooks installed"),
            payload={"ok": bool(result.get("ok"))},
        )

    return {"ok": all(r.get("ok", False) or r.get("skipped") for r in results), "server_url": url, "results": results}


def uninstall(agent: Optional[str] = None) -> dict[str, Any]:
    results: List[dict[str, Any]] = []
    target = registry if not agent else [h for h in registry if h.name == agent]
    if agent and not target:
        return {"ok": False, "error": f"unknown adapter: {agent}"}

    for h in target:
        result = h.uninstall_hooks()
        result.setdefault("agent", h.name)
        results.append(result)
        event_write(
            "agent.uninstall",
            agent=h.name,
            message=result.get("error") or "hooks removed",
            payload={"ok": bool(result.get("ok"))},
        )

    return {"ok": all(r.get("ok", False) for r in results), "results": results}


__all__ = ["install", "uninstall"]
