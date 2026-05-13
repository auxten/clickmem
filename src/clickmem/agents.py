"""Agent-adapter surface for the FastAPI server.

Iterates over :mod:`clickmem.adapters.registry` so every adapter is treated
uniformly. Per-agent telemetry (session counts, last seen) is sourced from
the ``events`` table, and install/uninstall/test calls round-trip through
the adapter handle's own implementation.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, List

from clickmem import raw as raw_mod
from clickmem.adapters import AdapterHandle, registry
from clickmem.backend import Backend, get_backend
from clickmem.events import activity_counts, write as event_write
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
    for h in registry:
        out.append(
            {
                "name": h.name,
                "label": h.label,
                "experimental": h.experimental,
                "discovered": h.detect(),
                "installed": False,  # populated by per-adapter status if we add it later
                "session_count_24h": _session_count(h.name, backend),
                "last_event": _last_event_at(h.name, backend),
            }
        )
    return out


def activity(name: str, hours: int = 24, backend: Backend | None = None) -> List[dict[str, Any]]:
    return activity_counts(hours=hours, agent=name, backend=backend)


def install(name: str, server_url: str = "") -> dict[str, Any]:
    h = _handle(name)
    if h is None:
        return {"ok": False, "agent": name, "error": "unknown adapter"}
    result = h.install_hooks(server_url)
    event_write(
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
    event_write(
        "agent.uninstall",
        agent=name,
        message=result.get("error") or "hooks removed",
        payload={"ok": bool(result.get("ok"))},
    )
    return result


def test(name: str) -> dict[str, Any]:
    """End-to-end smoke: POST a synthetic raw landing as this agent, then
    confirm the matching event row appears.
    """
    h = _handle(name)
    if h is None:
        return {"ok": False, "agent": name, "error": "unknown adapter"}

    backend = get_backend()
    session_id = f"clickmem-test-{uuid.uuid4().hex[:8]}"
    text = f"[clickmem.test] synthetic landing for adapter={name}; if you can see this in /v1/get-raw, the loop is healthy."

    raw_result = raw_mod.append(
        text,
        session_id=session_id,
        agent=name,
        project_id="",
        role="test",
        meta={"clickmem_test": True, "adapter": name},
        backend=backend,
    )

    rows = backend.query(
        "SELECT count() AS c FROM events "
        f"WHERE agent = {quote_str(name)} AND kind = 'raw.land' "
        f"AND positionCaseInsensitive(payload_json, {quote_str(session_id)}) > 0"
    )
    found = bool(rows) and int(rows[0].get("c", 0) or 0) > 0

    event_write(
        "agent.test",
        agent=name,
        message="end-to-end smoke",
        payload={
            "session_id": session_id,
            "raw_ok": bool(raw_result.get("ok")),
            "event_landed": found,
            "discovered": h.detect(),
        },
    )

    return {
        "ok": bool(raw_result.get("ok")) and found,
        "agent": name,
        "discovered": h.detect(),
        "experimental": h.experimental,
        "session_id": session_id,
        "event_landed": found,
        "raw_result": raw_result,
        "message": "round-tripped synthetic raw landing" if found else "raw landed but event row not observed",
    }
