"""Transport: `LocalTransport` calls domain functions in-process; `RemoteTransport`
hits the FastAPI server over HTTP. The CLI and the MCP server both go through
this layer so we can swap behaviour with a single env var.

When ``CLICKMEM_REMOTE`` is set, the CLI/MCP automatically use the remote
transport against that URL; otherwise they call into the local domain.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, List, Optional, Protocol

import httpx

from clickmem.config import get_config


class Transport(Protocol):
    def health(self) -> dict[str, Any]: ...
    def recall(self, query: str, **kwargs: Any) -> dict[str, Any]: ...
    def recall_trace(self, query: str, **kwargs: Any) -> dict[str, Any]: ...
    def remember(self, content: str, **kwargs: Any) -> dict[str, Any]: ...
    def edit(self, memory_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def forget(self, memory_id: str, reason: str = "", agent: str = "", scope: str = "personal") -> dict[str, Any]: ...
    def pin(self, memory_id: str, agent: str = "", scope: str = "personal") -> dict[str, Any]: ...
    def unpin(self, memory_id: str, agent: str = "", scope: str = "personal") -> dict[str, Any]: ...
    def show(self, memory_id: str, with_history: bool = False, with_neighbors: bool = False, scope: str = "personal") -> dict[str, Any]: ...
    def list_memories(self, **kwargs: Any) -> dict[str, Any]: ...
    def conflicts(self, project_id: Optional[str] = None, scope: str = "personal", limit: int = 200) -> List[dict[str, Any]]: ...
    def resolve(self, memory_id: str, op: str, peer_id: str = "", scope: str = "personal") -> dict[str, Any]: ...
    def blacklist_add(self, pattern: str, scope: str = "global", reason: str = "") -> dict[str, Any]: ...
    def blacklist_remove(self, blacklist_id: str) -> dict[str, Any]: ...
    def blacklist_list(self) -> List[dict[str, Any]]: ...
    def get_raw(self, session_id: Optional[str] = None, last: int = 50, agent: Optional[str] = None) -> List[dict[str, Any]]: ...
    def project_link(self, a: str, b: str, reason: str = "") -> dict[str, Any]: ...
    def projects_list(self) -> List[dict[str, Any]]: ...


class LocalTransport:
    """Direct in-process transport — calls domain modules synchronously."""

    def health(self) -> dict[str, Any]:
        from clickmem import __version__
        from clickmem.backend import get_backend

        try:
            get_backend().query("SELECT 1 AS ok")
            ok = True
        except Exception:
            ok = False
        cfg = get_config()
        return {
            "ok": ok,
            "version": __version__,
            "backend": cfg.backend,
            "embedding_model": cfg.embedding_model,
        }

    def recall(self, query: str, **kwargs: Any) -> dict[str, Any]:
        from clickmem.scopes import recall_scoped

        timeout_seconds = kwargs.pop("timeout_seconds", None)
        if timeout_seconds is not None:
            return _fail_open_call(
                lambda: recall_scoped(query, **kwargs),
                timeout_seconds=float(timeout_seconds),
                fallback_kind="recall",
            )
        return recall_scoped(query, **kwargs)

    def recall_trace(self, query: str, **kwargs: Any) -> dict[str, Any]:
        from clickmem.scopes import recall_trace_scoped

        timeout_seconds = kwargs.pop("timeout_seconds", None)
        if timeout_seconds is not None:
            return _fail_open_call(
                lambda: recall_trace_scoped(query, **kwargs),
                timeout_seconds=float(timeout_seconds),
                fallback_kind="recall_trace",
                query=query,
                filters={
                    "project_id": kwargs.get("project_id", ""),
                    "include_confidential": bool(kwargs.get("include_confidential", False)),
                    "cross_project": bool(kwargs.get("cross_project", False)),
                    "kind": kwargs.get("kind"),
                    "tags": list(kwargs.get("tags") or []),
                    "tag_mode": kwargs.get("tag_mode", "any"),
                    "scope": kwargs.get("scope", "all"),
                },
            )
        return recall_trace_scoped(query, **kwargs)

    def remember(self, content: str, **kwargs: Any) -> dict[str, Any]:
        from clickmem.memories import add
        from clickmem.scopes import normalise_write_scope, remember_project

        scope = normalise_write_scope(str(kwargs.pop("scope", "personal")))
        if scope == "project":
            return remember_project(content, **kwargs)
        result = add(content, **kwargs)
        result.setdefault("scope", "personal")
        return result

    def edit(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        from clickmem.memories import edit
        from clickmem.embedding import embed
        from clickmem.scopes import normalise_write_scope, run_project_operation

        scope = normalise_write_scope(str(kwargs.pop("scope", "personal")))
        if scope == "project":
            payload = {"memory_id": memory_id, **kwargs}
            if kwargs.get("content") is not None:
                payload["embedding"] = embed(str(kwargs["content"]).strip())
            return run_project_operation("edit", payload)
        result = edit(memory_id, **kwargs)
        result.setdefault("scope", "personal")
        return result

    def forget(self, memory_id: str, reason: str = "", agent: str = "", scope: str = "personal") -> dict[str, Any]:
        from clickmem.memories import forget
        from clickmem.scopes import normalise_write_scope, run_project_operation

        if normalise_write_scope(scope) == "project":
            return run_project_operation(
                "forget", {"memory_id": memory_id, "reason": reason, "agent": agent}
            )
        result = forget(memory_id, reason=reason, agent=agent)
        result.setdefault("scope", "personal")
        return result

    def pin(self, memory_id: str, agent: str = "", scope: str = "personal") -> dict[str, Any]:
        from clickmem.memories import pin
        from clickmem.scopes import normalise_write_scope, run_project_operation

        if normalise_write_scope(scope) == "project":
            return run_project_operation("pin", {"memory_id": memory_id, "agent": agent})
        result = pin(memory_id, agent=agent)
        result.setdefault("scope", "personal")
        return result

    def unpin(self, memory_id: str, agent: str = "", scope: str = "personal") -> dict[str, Any]:
        from clickmem.memories import unpin
        from clickmem.scopes import normalise_write_scope, run_project_operation

        if normalise_write_scope(scope) == "project":
            return run_project_operation("unpin", {"memory_id": memory_id, "agent": agent})
        result = unpin(memory_id, agent=agent)
        result.setdefault("scope", "personal")
        return result

    def show(self, memory_id: str, with_history: bool = False, with_neighbors: bool = False, scope: str = "personal") -> dict[str, Any]:
        from clickmem.history import history_with_diffs
        from clickmem.memories import get, neighbors
        from clickmem.scopes import normalise_write_scope, run_project_operation

        if normalise_write_scope(scope) == "project":
            out = run_project_operation("get", {"memory_id": memory_id})
            if with_history and out.get("status") != "missing":
                out["history"] = run_project_operation("history", {"memory_id": memory_id}).get("items", [])
            if with_neighbors and out.get("status") != "missing":
                out["neighbors"] = run_project_operation(
                    "neighbors", {"memory_id": memory_id, "limit": 10}
                ).get("neighbors", [])
            return out

        m = get(memory_id)
        if m is None:
            return {"status": "missing", "id": memory_id}
        memory = m.to_dict()
        memory["scope"] = "personal"
        out: dict[str, Any] = {"memory": memory, "scope": "personal"}
        if with_history:
            out["history"] = history_with_diffs(memory_id)
        if with_neighbors:
            out["neighbors"] = neighbors(memory_id)
        return out

    def list_memories(self, **kwargs: Any) -> dict[str, Any]:
        from clickmem.memories import list_paginated
        from clickmem.scopes import normalise_write_scope, run_project_operation

        scope = normalise_write_scope(str(kwargs.pop("scope", "personal")))
        if scope == "project":
            return run_project_operation("list", kwargs)
        result = list_paginated(**kwargs)
        for item in result.get("items", []):
            item["scope"] = "personal"
        result["scope"] = "personal"
        return result

    def conflicts(self, project_id: Optional[str] = None, scope: str = "personal", limit: int = 200) -> List[dict[str, Any]]:
        from clickmem.conflicts import list_conflicts
        from clickmem.scopes import normalise_write_scope, run_project_operation

        if normalise_write_scope(scope) == "project":
            return list(
                run_project_operation(
                    "conflicts", {"project_id": project_id, "limit": int(limit)}
                ).get("items", [])
            )
        items = list_conflicts(project_id=project_id, limit=int(limit))
        for item in items:
            item["scope"] = "personal"
        return items

    def resolve(self, memory_id: str, op: str, peer_id: str = "", scope: str = "personal") -> dict[str, Any]:
        from clickmem.conflicts import resolve
        from clickmem.scopes import normalise_write_scope, run_project_operation

        if normalise_write_scope(scope) == "project":
            return run_project_operation(
                "resolve", {"memory_id": memory_id, "op": op, "peer_id": peer_id}
            )
        result = resolve(memory_id, op, peer_id=peer_id)
        result.setdefault("scope", "personal")
        return result

    def blacklist_add(self, pattern: str, scope: str = "global", reason: str = "") -> dict[str, Any]:
        from clickmem.blacklist import add

        return add(pattern, scope=scope, reason=reason).to_dict()

    def blacklist_remove(self, blacklist_id: str) -> dict[str, Any]:
        from clickmem.blacklist import remove

        remove(blacklist_id)
        return {"ok": True, "id": blacklist_id}

    def blacklist_list(self) -> List[dict[str, Any]]:
        from clickmem.blacklist import list_all

        return [b.to_dict() for b in list_all()]

    def get_raw(self, session_id: Optional[str] = None, last: int = 50, agent: Optional[str] = None) -> List[dict[str, Any]]:
        from clickmem.raw import get_raw

        return get_raw(session_id=session_id, last=last, agent=agent)

    def project_link(self, a: str, b: str, reason: str = "") -> dict[str, Any]:
        from clickmem.projects import link

        pa, pb = link(a, b, reason=reason)
        return {"a": pa.to_dict(), "b": pb.to_dict()}

    def projects_list(self) -> List[dict[str, Any]]:
        from clickmem.projects import list_all

        return [p.to_dict() for p in list_all()]


class RemoteTransport:
    """HTTP transport against a ClickMem server (set via ``CLICKMEM_REMOTE``)."""

    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, headers=self._headers())

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Optional[dict[str, Any]] = None, timeout: float | None = None) -> Any:
        r = self._client.post(path, json=body or {}, timeout=timeout)
        if r.status_code == 409:
            payload = r.json()
            return payload.get("detail", payload)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        r = self._client.patch(path, json=body or {})
        if r.status_code == 409:
            payload = r.json()
            return payload.get("detail", payload)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        r = self._client.delete(path, params=params)
        if r.status_code == 409:
            payload = r.json()
            return payload.get("detail", payload)
        r.raise_for_status()
        if r.content:
            return r.json()
        return {"ok": True}

    def health(self) -> dict[str, Any]:
        return self._get("/v1/health")

    def recall(self, query: str, **kwargs: Any) -> dict[str, Any]:
        timeout_seconds = kwargs.get("timeout_seconds")
        if timeout_seconds is not None:
            try:
                return self._post("/v1/recall", {"query": query, **kwargs}, timeout=float(timeout_seconds))
            except Exception as e:  # noqa: BLE001
                return _recall_fallback("recall", float(timeout_seconds), e)
        return self._post("/v1/recall", {"query": query, **kwargs})

    def recall_trace(self, query: str, **kwargs: Any) -> dict[str, Any]:
        timeout_seconds = kwargs.get("timeout_seconds")
        if timeout_seconds is not None:
            try:
                return self._post("/v1/recall/trace", {"query": query, **kwargs}, timeout=float(timeout_seconds))
            except Exception as e:  # noqa: BLE001
                return _recall_trace_fallback(query, kwargs, float(timeout_seconds), e)
        return self._post("/v1/recall/trace", {"query": query, **kwargs})

    def remember(self, content: str, **kwargs: Any) -> dict[str, Any]:
        return self._post("/v1/memories", {"content": content, **kwargs})

    def edit(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._patch(f"/v1/memories/{memory_id}", kwargs)

    def forget(self, memory_id: str, reason: str = "", agent: str = "", scope: str = "personal") -> dict[str, Any]:
        return self._delete(
            f"/v1/memories/{memory_id}",
            params={"reason": reason, "agent": agent, "scope": scope},
        )

    def pin(self, memory_id: str, agent: str = "", scope: str = "personal") -> dict[str, Any]:
        return self._patch(
            f"/v1/memories/{memory_id}", {"pinned": True, "agent": agent, "scope": scope}
        )

    def unpin(self, memory_id: str, agent: str = "", scope: str = "personal") -> dict[str, Any]:
        return self._patch(
            f"/v1/memories/{memory_id}", {"pinned": False, "agent": agent, "scope": scope}
        )

    def show(self, memory_id: str, with_history: bool = False, with_neighbors: bool = False, scope: str = "personal") -> dict[str, Any]:
        try:
            mem = self._get(f"/v1/memories/{memory_id}", params={"scope": scope})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"status": "missing", "id": memory_id}
            raise
        out: dict[str, Any] = {"memory": mem, "scope": scope}
        if with_history:
            out["history"] = self._get(
                f"/v1/memories/{memory_id}/history", params={"scope": scope}
            )
        if with_neighbors:
            out["neighbors"] = self._get(
                f"/v1/memories/{memory_id}/neighbors", params={"scope": scope}
            )
        return out

    def list_memories(self, **kwargs: Any) -> dict[str, Any]:
        return self._get("/v1/memories", params={k: v for k, v in kwargs.items() if v is not None})

    def conflicts(self, project_id: Optional[str] = None, scope: str = "personal", limit: int = 200) -> List[dict[str, Any]]:
        params = {"scope": scope, "limit": int(limit)}
        if project_id:
            params["project_id"] = project_id
        return self._get("/v1/conflicts", params=params)

    def resolve(self, memory_id: str, op: str, peer_id: str = "", scope: str = "personal") -> dict[str, Any]:
        return self._post(
            f"/v1/conflicts/{memory_id}/resolve",
            {"op": op, "peer_id": peer_id, "scope": scope},
        )

    def blacklist_add(self, pattern: str, scope: str = "global", reason: str = "") -> dict[str, Any]:
        return self._post("/v1/blacklist", {"pattern": pattern, "scope": scope, "reason": reason})

    def blacklist_remove(self, blacklist_id: str) -> dict[str, Any]:
        return self._delete(f"/v1/blacklist/{blacklist_id}")

    def blacklist_list(self) -> List[dict[str, Any]]:
        return self._get("/v1/blacklist")

    def get_raw(self, session_id: Optional[str] = None, last: int = 50, agent: Optional[str] = None) -> List[dict[str, Any]]:
        return self._get(
            "/v1/get_raw",
            params={k: v for k, v in {"session_id": session_id, "last": last, "agent": agent}.items() if v is not None},
        )

    def project_link(self, a: str, b: str, reason: str = "") -> dict[str, Any]:
        return self._post("/v1/projects/link", {"a": a, "b": b, "reason": reason})

    def projects_list(self) -> List[dict[str, Any]]:
        return self._get("/v1/projects")


def get_transport() -> Transport:
    """Return the right transport based on ``CLICKMEM_REMOTE``."""
    cfg = get_config(refresh=True)
    if cfg.remote_url:
        return RemoteTransport(cfg.remote_url, api_key=cfg.api_key)
    return LocalTransport()


def _recall_fallback(kind: str, timeout_seconds: float, error: Exception | None = None) -> dict[str, Any]:
    message = f"{kind} failed or timed out after {timeout_seconds:.1f}s; continuing without memory context"
    if error is not None:
        message = f"{message}: {error}"
    return {"hits": [], "timeout": True, "warning": message}


def _recall_trace_fallback(
    query: str,
    kwargs: dict[str, Any],
    timeout_seconds: float,
    error: Exception | None = None,
) -> dict[str, Any]:
    fallback = _recall_fallback("recall_trace", timeout_seconds, error)
    return {
        "query": query,
        "filters": {
            "project_id": kwargs.get("project_id", ""),
            "include_confidential": bool(kwargs.get("include_confidential", False)),
            "cross_project": bool(kwargs.get("cross_project", False)),
            "kind": kwargs.get("kind"),
            "tags": list(kwargs.get("tags") or []),
            "tag_mode": kwargs.get("tag_mode", "any"),
            "scope": kwargs.get("scope", "all"),
        },
        "hits": [],
        "candidates": [],
        "timeout": True,
        "warning": fallback["warning"],
    }


def _fail_open_call(
    fn: Callable[[], dict[str, Any]],
    *,
    timeout_seconds: float,
    fallback_kind: str,
    query: str = "",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result_queue: queue.Queue[tuple[str, dict[str, Any] | Exception]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result_queue.put(("ok", fn()), block=False)
        except Exception as e:  # noqa: BLE001
            result_queue.put(("error", e), block=False)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        status, payload = result_queue.get(timeout=max(0.1, float(timeout_seconds)))
    except queue.Empty:
        if fallback_kind == "recall_trace":
            return _recall_trace_fallback(query, filters or {}, timeout_seconds)
        return _recall_fallback(fallback_kind, timeout_seconds)
    if status == "ok":
        assert isinstance(payload, dict)
        payload.setdefault("timeout", False)
        return payload
    assert isinstance(payload, Exception)
    if fallback_kind == "recall_trace":
        return _recall_trace_fallback(query, filters or {}, timeout_seconds, payload)
    return _recall_fallback(fallback_kind, timeout_seconds, payload)
