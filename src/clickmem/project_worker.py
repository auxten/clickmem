"""One-shot helper for shared project-memory operations.

Input and output are one JSON object on stdin/stdout.  This module is an
internal implementation detail launched by :mod:`clickmem.scopes`; it is not a
separate service and owns no persistent process or port.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from clickmem.backend.durable import DurableBackend
from clickmem.config import get_config


class ProjectStoreBusy(RuntimeError):
    pass


def _open_backend(*, read_only: bool) -> DurableBackend:
    cfg = get_config(refresh=True)
    if not cfg.project_durable_url:
        raise RuntimeError("CLICKMEM_PROJECT_DURABLE_URL is not set")

    deadline = time.monotonic() + max(0.0, float(cfg.project_write_timeout_seconds))
    while True:
        try:
            return DurableBackend(
                url=cfg.project_durable_url,
                oid=cfg.project_durable_id,
                embed_dim=cfg.embedding_dim,
                db=cfg.ch_database,
                owner="clickmem-project",
                read_only=read_only,
                flush_on_execute=False,
            )
        except Exception as exc:  # chdb.durable.LeaseError is optional at import time
            if read_only or exc.__class__.__name__ != "LeaseError":
                raise
            if time.monotonic() >= deadline:
                raise ProjectStoreBusy(str(exc)) from exc
            time.sleep(0.05)


def _missing_schema(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "unknown_table" in text or "doesn't exist" in text or "does not exist" in text
    )


def _annotate_scope(result: dict[str, Any]) -> dict[str, Any]:
    result.setdefault("scope", "project")
    if isinstance(result.get("memory"), dict):
        result["memory"].setdefault("scope", "project")
    for key in ("items", "hits", "candidates", "neighbors"):
        for item in result.get(key) or []:
            if isinstance(item, dict):
                item.setdefault("scope", "project")
    return result


def handle(request: dict[str, Any]) -> dict[str, Any]:
    from clickmem import conflicts, history, memories
    from clickmem.recall import recall, recall_trace

    op = str(request.get("op") or "")
    payload = dict(request.get("payload") or {})
    read_only = op in {
        "recall",
        "recall_trace",
        "get",
        "list",
        "conflicts",
        "neighbors",
        "history",
    }
    backend = _open_backend(read_only=read_only)
    try:
        try:
            if op == "remember":
                result = memories.add(
                    payload.pop("content"),
                    embedding_vector=payload.pop("embedding", None),
                    backend=backend,
                    **payload,
                )
                return _annotate_scope(result)
            if op == "recall":
                query = str(payload.pop("query", ""))
                hits = recall(
                    query,
                    query_vector=payload.pop("query_vector", None),
                    scope="project",
                    write_event=False,
                    backend=backend,
                    **payload,
                )
                return {"hits": [hit.to_dict() for hit in hits], "scope": "project"}
            if op == "recall_trace":
                query = str(payload.pop("query", ""))
                return _annotate_scope(
                    recall_trace(
                        query,
                        query_vector=payload.pop("query_vector", None),
                        scope="project",
                        backend=backend,
                        **payload,
                    )
                )
            if op == "get":
                item = memories.get(str(payload["memory_id"]), backend=backend)
                return _annotate_scope(
                    {"status": "ok", "memory": item.to_dict()}
                    if item is not None
                    else {"status": "missing", "id": str(payload["memory_id"])}
                )
            if op == "list":
                return _annotate_scope(
                    memories.list_paginated(backend=backend, **payload)
                )
            if op == "edit":
                memory_id = str(payload.pop("memory_id"))
                result = memories.edit(
                    memory_id,
                    embedding_vector=payload.pop("embedding", None),
                    backend=backend,
                    **payload,
                )
                return _annotate_scope(result)
            if op == "forget":
                memory_id = str(payload.pop("memory_id"))
                return _annotate_scope(
                    memories.forget(memory_id, backend=backend, **payload)
                )
            if op == "pin":
                memory_id = str(payload.pop("memory_id"))
                return _annotate_scope(
                    memories.pin(memory_id, backend=backend, **payload)
                )
            if op == "unpin":
                memory_id = str(payload.pop("memory_id"))
                return _annotate_scope(
                    memories.unpin(memory_id, backend=backend, **payload)
                )
            if op == "bulk":
                return _annotate_scope(memories.bulk(backend=backend, **payload))
            if op == "conflicts":
                return _annotate_scope(
                    {"items": conflicts.list_conflicts(backend=backend, **payload)}
                )
            if op == "resolve":
                memory_id = str(payload.pop("memory_id"))
                return _annotate_scope(
                    conflicts.resolve(memory_id, backend=backend, **payload)
                )
            if op == "neighbors":
                memory_id = str(payload.pop("memory_id"))
                return _annotate_scope(
                    {
                        "neighbors": memories.neighbors(
                            memory_id, backend=backend, **payload
                        )
                    }
                )
            if op == "history":
                return {
                    "items": history.history_with_diffs(
                        str(payload["memory_id"]), backend=backend
                    ),
                    "scope": "project",
                }
            raise ValueError(f"unknown project operation: {op!r}")
        except Exception as exc:
            if read_only and _missing_schema(exc):
                if op in {"recall", "list", "conflicts", "neighbors", "history"}:
                    key = {
                        "recall": "hits",
                        "neighbors": "neighbors",
                    }.get(op, "items")
                    return {key: [], "scope": "project"}
                if op == "recall_trace":
                    return {
                        "query": payload.get("query", ""),
                        "hits": [],
                        "candidates": [],
                        "scope": "project",
                    }
                if op == "get":
                    return {
                        "status": "missing",
                        "id": str(payload.get("memory_id", "")),
                        "scope": "project",
                    }
            raise
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            if not read_only:
                if active_error:
                    backend.discard_pending()
                else:
                    try:
                        backend.commit()
                    except BaseException as exc:
                        backend.discard_pending()
                        if exc.__class__.__name__ == "LeaseError":
                            raise ProjectStoreBusy(str(exc)) from exc
                        raise
        finally:
            backend.close()


def main() -> None:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        result = handle(request)
    except ProjectStoreBusy as exc:
        result = {
            "status": "cas_conflict",
            "scope": "project",
            "message": "project memory changed concurrently; retry against the latest revision",
            "detail": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "error",
            "message": str(exc),
            "type": exc.__class__.__name__,
        }
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
