"""Route a small set of CLI write paths to a live server when one is up.

The ``clickmem hooks install`` and ``clickmem agents --test`` CLI flows used to
open the local chDB backend directly. When the ClickMem server is already
running on the same host it holds an exclusive file lock on ``~/.clickmem/data/``,
so the CLI process crashes with ``Code: 76. DB::Exception: Cannot lock file``.

This shim wraps the two operations those CLI paths actually need
(``event_write`` and ``raw_append``) and decides per call whether to:

* talk to a running server over HTTP via :class:`clickmem.transport.RemoteTransport`
  (when ``CLICKMEM_REMOTE`` is set, or a server is reachable on
  ``CLICKMEM_SERVER_HOST:CLICKMEM_SERVER_PORT``), or
* fall through to the local domain modules (no server running).

The FastAPI server itself opts out by calling :func:`mark_in_server_process`
at app-construction time — its endpoints continue to write through the
in-process backend directly, so they never round-trip HTTP to themselves.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Tuple

from clickmem.config import get_config
from clickmem.transport import RemoteTransport

_log = logging.getLogger(__name__)


# When True the shim never auto-probes (the server's own endpoints use the
# in-process backend directly and must not HTTP-call themselves).
_in_server_process: bool = False

# Probe cache: (url-or-None, monotonic-time). Cleared by reset().
_probe_cache: Optional[Tuple[Optional[str], float]] = None

_PROBE_TTL_SEC = 5.0
_PROBE_TIMEOUT_SEC = 0.5
_REMOTE_TIMEOUT_SEC = 15.0


def mark_in_server_process() -> None:
    """Disable auto-probe inside the running ClickMem server process."""
    global _in_server_process, _probe_cache
    _in_server_process = True
    _probe_cache = None


def reset() -> None:
    """Clear cached state (probe cache + server-process flag). Used by tests."""
    global _in_server_process, _probe_cache
    _in_server_process = False
    _probe_cache = None


def _resolve_remote_url() -> Optional[str]:
    """Return the URL of a reachable ClickMem server, or ``None`` for local."""
    cfg = get_config(refresh=True)
    if cfg.remote_url:
        return cfg.remote_url.rstrip("/")
    if _in_server_process:
        return None

    global _probe_cache
    now = time.monotonic()
    if _probe_cache is not None and (now - _probe_cache[1]) < _PROBE_TTL_SEC:
        return _probe_cache[0]

    url = f"http://{cfg.server_host}:{cfg.server_port}"
    try:
        rt = RemoteTransport(url, api_key=cfg.api_key, timeout=_PROBE_TIMEOUT_SEC)
        info = rt.health()
        if info.get("ok"):
            _probe_cache = (url, now)
            return url
    except Exception as e:  # noqa: BLE001
        _log.debug("clickmem server probe failed at %s: %s", url, e)
    _probe_cache = (None, now)
    return None


def _remote_client() -> Optional[RemoteTransport]:
    url = _resolve_remote_url()
    if not url:
        return None
    cfg = get_config()
    return RemoteTransport(url, api_key=cfg.api_key, timeout=_REMOTE_TIMEOUT_SEC)


def event_write(
    kind: str,
    *,
    agent: str = "",
    project_id: str = "",
    memory_id: str = "",
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one row to the ``events`` table via the right transport.

    Errors are logged but never raised — matches :func:`clickmem.events.write`.
    """
    rt = _remote_client()
    if rt is not None:
        try:
            rt._post(
                "/v1/events",
                {
                    "kind": kind,
                    "agent": agent,
                    "project_id": project_id,
                    "memory_id": memory_id,
                    "message": message,
                    "payload": payload or {},
                },
            )
            return
        except Exception as e:  # noqa: BLE001
            _log.warning("remote event_write(%s) failed; falling back to local: %s", kind, e)

    from clickmem.events import write as local_write

    local_write(
        kind,
        agent=agent,
        project_id=project_id,
        memory_id=memory_id,
        message=message,
        payload=payload,
    )


def raw_append(
    text: str,
    *,
    session_id: str,
    agent: str = "",
    project_id: str = "",
    role: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert one raw row via the right transport.

    Returns the same shape :func:`clickmem.raw.append` does locally.
    """
    rt = _remote_client()
    if rt is not None:
        try:
            return rt._post(
                "/v1/raw",
                {
                    "text": text,
                    "session_id": session_id,
                    "agent": agent,
                    "project_id": project_id,
                    "role": role,
                    "meta": meta or {},
                },
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("remote raw_append failed; falling back to local: %s", e)

    from clickmem.raw import append as local_append

    return local_append(
        text,
        session_id=session_id,
        agent=agent,
        project_id=project_id,
        role=role,
        meta=meta,
    )


__all__ = [
    "event_write",
    "raw_append",
    "mark_in_server_process",
    "reset",
]
