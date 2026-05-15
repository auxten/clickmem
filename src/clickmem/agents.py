"""Agent-adapter surface for the FastAPI server.

Iterates over :mod:`clickmem.adapters.registry` so every adapter is treated
uniformly. Per-agent telemetry (session counts, last seen) is sourced from
the ``events`` table, and install/uninstall/test calls round-trip through
the adapter handle's own implementation.
"""

from __future__ import annotations

import logging
import socket
import uuid
from typing import Any, List

from clickmem import local_or_remote
from clickmem.adapters import AdapterHandle, registry
from clickmem.backend import Backend, get_backend
from clickmem.events import activity_counts
from clickmem.skill_install import install_clickmem_skill
from clickmem.sqlutil import quote_str


_log = logging.getLogger(__name__)


def _session_count(name: str, backend: Backend) -> int:
    try:
        rows = backend.query(
            "SELECT count() AS c FROM events "
            f"WHERE agent = {quote_str(name)} AND created_at >= now() - INTERVAL 24 HOUR"
        )
        return int(rows[0]["c"]) if rows else 0
    except Exception as e:  # noqa: BLE001
        _log.debug("agents._session_count failed: %s", e)
        return 0


def _last_event_at(name: str, backend: Backend) -> str:
    try:
        rows = backend.query(
            "SELECT toString(max(created_at)) AS t FROM events "
            f"WHERE agent = {quote_str(name)}"
        )
        return str(rows[0].get("t", "")) if rows else ""
    except Exception as e:  # noqa: BLE001
        _log.debug("agents._last_event_at failed: %s", e)
        return ""


def _handle(name: str) -> AdapterHandle | None:
    for h in registry:
        if h.name == name:
            return h
    return None


def list_agents(backend: Backend | None = None) -> List[dict[str, Any]]:
    backend = backend or get_backend()
    out: list[dict[str, Any]] = []
    host = socket.gethostname() or "localhost"
    for h in registry:
        discovered = h.detect()
        if not discovered:
            continue
        out.append(
            {
                "name": h.name,
                "label": h.label,
                "experimental": h.experimental,
                "discovered": discovered,
                "installed": discovered,
                "host": host,
                "session_count_24h": _session_count(h.name, backend),
                "last_event": _last_event_at(h.name, backend),
            }
        )
    return out


def activity(name: str, hours: int = 24, backend: Backend | None = None) -> List[dict[str, Any]]:
    return activity_counts(hours=hours, agent=name, backend=backend)


def install(name: str, server_url: str = "") -> dict[str, Any]:
    """Install only the live adapter hooks.

    Historical docs/rules from other agents are intentionally imported through
    ``import-docs`` or ``/v1/imports/{name}/run`` so install stays reversible.
    """
    h = _handle(name)
    if h is None:
        return {"ok": False, "agent": name, "error": "unknown adapter"}
    result = h.install_hooks(server_url)
    skill_result = install_clickmem_skill(name)
    if skill_result.get("installed") or skill_result.get("skipped"):
        result["skill"] = skill_result
    result.setdefault("imported", False)
    result.setdefault("import_hint", f"run `clickmem import-docs` or POST /v1/imports/{name}/run explicitly")
    local_or_remote.event_write(
        "agent.install",
        agent=name,
        message=result.get("error") or "hooks installed",
        payload={"ok": bool(result.get("ok")), "installed": bool(result.get("installed"))},
    )
    return result


def uninstall(name: str) -> dict[str, Any]:
    h = _handle(name)
    if h is None:
        return {"ok": False, "agent": name, "error": "unknown adapter"}
    result = h.uninstall_hooks()
    local_or_remote.event_write(
        "agent.uninstall",
        agent=name,
        message=result.get("error") or "hooks removed",
        payload={"ok": bool(result.get("ok"))},
    )
    return result


def test(name: str) -> dict[str, Any]:
    """End-to-end smoke: POST a synthetic raw landing as this agent and trust
    the response.

    Routed through :mod:`clickmem.local_or_remote`, so the CLI works on a host
    where the ClickMem server already owns the chDB file lock. When the call
    lands successfully, the server (or local backend) has written both the
    ``raw_transcripts`` row and the matching ``raw.land`` event in one shot.
    """
    h = _handle(name)
    if h is None:
        return {"ok": False, "agent": name, "error": "unknown adapter"}

    session_id = f"clickmem-test-{uuid.uuid4().hex[:8]}"
    text = f"[clickmem.test] synthetic landing for adapter={name}; if you can see this in /v1/get-raw, the loop is healthy."

    raw_result = local_or_remote.raw_append(
        text,
        session_id=session_id,
        agent=name,
        project_id="",
        role="test",
        meta={"clickmem_test": True, "adapter": name},
    )

    landed = bool(raw_result.get("ok")) and not raw_result.get("skipped")

    local_or_remote.event_write(
        "agent.test",
        agent=name,
        message="end-to-end smoke",
        payload={
            "session_id": session_id,
            "raw_ok": bool(raw_result.get("ok")),
            "event_landed": landed,
            "discovered": h.detect(),
        },
    )

    return {
        "ok": landed,
        "agent": name,
        "discovered": h.detect(),
        "experimental": h.experimental,
        "session_id": session_id,
        "event_landed": landed,
        "raw_result": raw_result,
        "message": "round-tripped synthetic raw landing" if landed else "raw landing skipped or failed",
    }
