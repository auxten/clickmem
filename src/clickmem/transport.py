"""Transport: `LocalTransport` calls domain functions in-process; `RemoteTransport`
hits the FastAPI server over HTTP. The CLI and the MCP server both go through
this layer so we can swap behaviour with a single env var.

When ``CLICKMEM_REMOTE`` is set, the CLI/MCP automatically use the remote
transport against that URL; otherwise they call into the local domain.
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol

import httpx

from clickmem.config import get_config


class Transport(Protocol):
    def health(self) -> dict[str, Any]: ...
    def recall(self, query: str, **kwargs: Any) -> dict[str, Any]: ...
    def recall_trace(self, query: str, **kwargs: Any) -> dict[str, Any]: ...
    def remember(self, content: str, **kwargs: Any) -> dict[str, Any]: ...
    def edit(self, memory_id: str, **kwargs: Any) -> dict[str, Any]: ...
    def forget(self, memory_id: str, reason: str = "", agent: str = "") -> dict[str, Any]: ...
    def pin(self, memory_id: str, agent: str = "") -> dict[str, Any]: ...
    def unpin(self, memory_id: str, agent: str = "") -> dict[str, Any]: ...
    def show(self, memory_id: str, with_history: bool = False, with_neighbors: bool = False) -> dict[str, Any]: ...
    def list_memories(self, **kwargs: Any) -> dict[str, Any]: ...
    def conflicts(self, project_id: Optional[str] = None) -> List[dict[str, Any]]: ...
    def resolve(self, memory_id: str, op: str, peer_id: str = "") -> dict[str, Any]: ...
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
        from clickmem.recall import recall

        hits = recall(query, **kwargs)
        return {"hits": [h.to_dict() for h in hits]}

    def recall_trace(self, query: str, **kwargs: Any) -> dict[str, Any]:
        from clickmem.recall import recall_trace

        return recall_trace(query, **kwargs)

    def remember(self, content: str, **kwargs: Any) -> dict[str, Any]:
        from clickmem.memories import add

        return add(content, **kwargs)

    def edit(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        from clickmem.memories import edit

        return edit(memory_id, **kwargs)

    def forget(self, memory_id: str, reason: str = "", agent: str = "") -> dict[str, Any]:
        from clickmem.memories import forget

        return forget(memory_id, reason=reason, agent=agent)

    def pin(self, memory_id: str, agent: str = "") -> dict[str, Any]:
        from clickmem.memories import pin

        return pin(memory_id, agent=agent)

    def unpin(self, memory_id: str, agent: str = "") -> dict[str, Any]:
        from clickmem.memories import unpin

        return unpin(memory_id, agent=agent)

    def show(self, memory_id: str, with_history: bool = False, with_neighbors: bool = False) -> dict[str, Any]:
        from clickmem.history import history_with_diffs
        from clickmem.memories import get, neighbors

        m = get(memory_id)
        if m is None:
            return {"status": "missing", "id": memory_id}
        out: dict[str, Any] = {"memory": m.to_dict()}
        if with_history:
            out["history"] = history_with_diffs(memory_id)
        if with_neighbors:
            out["neighbors"] = neighbors(memory_id)
        return out

    def list_memories(self, **kwargs: Any) -> dict[str, Any]:
        from clickmem.memories import list_paginated

        return list_paginated(**kwargs)

    def conflicts(self, project_id: Optional[str] = None) -> List[dict[str, Any]]:
        from clickmem.conflicts import list_conflicts

        return list_conflicts(project_id=project_id)

    def resolve(self, memory_id: str, op: str, peer_id: str = "") -> dict[str, Any]:
        from clickmem.conflicts import resolve

        return resolve(memory_id, op, peer_id=peer_id)

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

    def _post(self, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        r = self._client.post(path, json=body or {})
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: Optional[dict[str, Any]] = None) -> Any:
        r = self._client.patch(path, json=body or {})
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        r = self._client.delete(path, params=params)
        r.raise_for_status()
        if r.content:
            return r.json()
        return {"ok": True}

    def health(self) -> dict[str, Any]:
        return self._get("/v1/health")

    def recall(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self._post("/v1/recall", {"query": query, **kwargs})

    def recall_trace(self, query: str, **kwargs: Any) -> dict[str, Any]:
        return self._post("/v1/recall/trace", {"query": query, **kwargs})

    def remember(self, content: str, **kwargs: Any) -> dict[str, Any]:
        return self._post("/v1/memories", {"content": content, **kwargs})

    def edit(self, memory_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._patch(f"/v1/memories/{memory_id}", kwargs)

    def forget(self, memory_id: str, reason: str = "", agent: str = "") -> dict[str, Any]:
        return self._delete(f"/v1/memories/{memory_id}", params={"reason": reason, "agent": agent})

    def pin(self, memory_id: str, agent: str = "") -> dict[str, Any]:
        return self._patch(f"/v1/memories/{memory_id}", {"pinned": True, "agent": agent})

    def unpin(self, memory_id: str, agent: str = "") -> dict[str, Any]:
        return self._patch(f"/v1/memories/{memory_id}", {"pinned": False, "agent": agent})

    def show(self, memory_id: str, with_history: bool = False, with_neighbors: bool = False) -> dict[str, Any]:
        try:
            mem = self._get(f"/v1/memories/{memory_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"status": "missing", "id": memory_id}
            raise
        out: dict[str, Any] = {"memory": mem}
        if with_history:
            out["history"] = self._get(f"/v1/memories/{memory_id}/history")
        if with_neighbors:
            out["neighbors"] = self._get(f"/v1/memories/{memory_id}/neighbors")
        return out

    def list_memories(self, **kwargs: Any) -> dict[str, Any]:
        return self._get("/v1/memories", params={k: v for k, v in kwargs.items() if v is not None})

    def conflicts(self, project_id: Optional[str] = None) -> List[dict[str, Any]]:
        params = {"project_id": project_id} if project_id else None
        return self._get("/v1/conflicts", params=params)

    def resolve(self, memory_id: str, op: str, peer_id: str = "") -> dict[str, Any]:
        return self._post(f"/v1/conflicts/{memory_id}/resolve", {"op": op, "peer_id": peer_id})

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
