"""Transport abstraction — local or remote access to ClickMem memory operations.

LocalTransport: direct in-process calls to memory_core (default, existing behavior).
RemoteTransport: HTTP calls to a ClickMem REST API server (for LAN/remote access).
"""

from __future__ import annotations

import os
from typing import Optional, Protocol

from memory_core.models import Memory, RetrievalConfig


class Transport(Protocol):
    """Protocol defining the memory operation interface."""

    def recall(self, query: str, cfg: RetrievalConfig | None = None,
               min_score: float = 0.0) -> list[dict]: ...

    def remember(self, content: str, layer: str = "semantic",
                 category: str = "knowledge", tags: list[str] | None = None,
                 no_upsert: bool = False) -> dict: ...

    def extract(self, text: str, session_id: str = "") -> list[str]: ...

    def forget(self, memory_id: str) -> dict: ...

    def review(self, layer: str = "semantic", limit: int = 100) -> list[Memory] | str | None: ...

    def status(self) -> dict: ...

    def maintain(self, dry_run: bool = False) -> dict: ...

    def sql(self, query: str) -> list[dict]: ...

    def health(self) -> dict: ...


class LocalTransport:
    """Direct in-process access — current behavior, no network overhead."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.environ.get(
            "CLICKMEM_DB_PATH",
            os.path.expanduser("~/.openclaw/memory/chdb-data"),
        )
        self._db = None
        self._emb = None

    def _get_db(self):
        if self._db is None:
            from memory_core.db import MemoryDB
            self._db = MemoryDB(self._db_path)
        return self._db

    def _get_emb(self):
        if self._emb is None:
            try:
                from memory_core.embedding import EmbeddingEngine
                self._emb = EmbeddingEngine()
                self._emb.load()
            except Exception:
                from tests.helpers.mock_embedding import MockEmbeddingEngine
                self._emb = MockEmbeddingEngine(dimension=256)
                self._emb.load()
        return self._emb

    def recall(self, query: str, cfg: RetrievalConfig | None = None,
               min_score: float = 0.0) -> list[dict]:
        from memory_core.retrieval import hybrid_search
        db = self._get_db()
        emb = self._get_emb()
        results = hybrid_search(db, emb, query, cfg=cfg)
        if min_score > 0:
            results = [r for r in results if r.get("final_score", 0) >= min_score]
        for r in results:
            try:
                db.touch(r["id"])
            except Exception:
                pass
        return results

    def remember(self, content: str, layer: str = "semantic",
                 category: str = "knowledge", tags: list[str] | None = None,
                 no_upsert: bool = False) -> dict:
        db = self._get_db()
        emb = self._get_emb()
        tag_list = tags or []

        if layer == "working":
            mid = db.set_working(content)
            return {"id": mid, "layer": "working", "status": "stored"}

        if no_upsert or layer == "episodic":
            m = Memory(
                content=content, layer=layer, category=category,
                tags=tag_list, embedding=emb.encode_document(content),
                source="api",
            )
            db.insert(m)
            return {"id": m.id, "layer": layer, "category": category, "status": "stored"}

        from memory_core.upsert import upsert
        from memory_core.llm import get_llm_complete
        llm = get_llm_complete()
        if llm is None:
            m = Memory(
                content=content, layer=layer, category=category,
                tags=tag_list, embedding=emb.encode_document(content),
                source="api",
            )
            db.insert(m)
            return {"id": m.id, "layer": layer, "category": category, "status": "stored",
                    "action": "ADD", "note": "no LLM available, direct insert"}

        result = upsert(db, emb, content, layer, category, tag_list, llm_complete=llm)
        return {
            "action": result.action, "id": result.added_id,
            "layer": layer, "category": category, "status": "stored",
            "updated": result.updated, "deleted": result.deleted,
        }

    def extract(self, text: str, session_id: str = "") -> list[str]:
        db = self._get_db()
        emb = self._get_emb()

        from memory_core.llm import get_llm_complete
        llm_complete = get_llm_complete()

        if llm_complete is None:
            m = Memory(
                content=text, layer="episodic", category="event",
                embedding=emb.encode_document(text),
                session_id=session_id, source="agent",
            )
            db.insert(m)
            return [m.id]

        from memory_core.extractor import MemoryExtractor
        extractor = MemoryExtractor(db, emb)
        return extractor.extract(
            [{"role": "user", "content": text}],
            llm_complete, session_id=session_id,
        )

    def forget(self, memory_id: str) -> dict:
        import re
        db = self._get_db()
        uuid_pattern = re.compile(r'^[0-9a-f]{8}(-[0-9a-f]{4}){0,3}', re.IGNORECASE)
        m = None
        looks_like_uuid = bool(uuid_pattern.match(memory_id))

        if looks_like_uuid:
            m = db.get(memory_id)
            if m is None:
                rows = db.query(
                    f"SELECT id FROM memories "
                    f"WHERE startsWith(id, '{db._escape(memory_id)}') AND is_active = 1 LIMIT 1"
                )
                if rows:
                    memory_id = rows[0]["id"]
                    m = db.get(memory_id)

        if m is None:
            emb = self._get_emb()
            from memory_core.retrieval import hybrid_search
            cfg = RetrievalConfig(top_k=1, layer="semantic")
            results = hybrid_search(db, emb, memory_id, cfg=cfg)
            if results and results[0].get("final_score", 0) > 0.3:
                found_id = results[0]["id"]
                m = db.get(found_id)
                if m:
                    memory_id = found_id

        if m is None:
            return {"error": "not found", "query": memory_id}

        db.deactivate(memory_id)
        return {"id": memory_id, "content": m.content, "status": "deleted"}

    def review(self, layer: str = "semantic", limit: int = 100) -> list[Memory] | str | None:
        db = self._get_db()
        if layer == "working":
            return db.get_working()
        return db.list_by_layer(layer, limit=limit)

    def status(self) -> dict:
        db = self._get_db()
        counts = db.count_by_layer()
        total = db.count()
        breakdown = db.stats()
        return {"counts": counts, "total": total, "breakdown": breakdown}

    def maintain(self, dry_run: bool = False) -> dict:
        db = self._get_db()
        if dry_run:
            stale = db.find_stale_episodic()
            deleted = db.find_deleted()
            tag_freqs = db.get_tag_frequencies()
            return {
                "dry_run": True,
                "would_clean_stale": len(stale),
                "would_purge_deleted": len(deleted),
                "promotion_candidates": dict(tag_freqs),
            }

        from memory_core.maintenance_mod import maintenance as maint
        emb = self._get_emb()

        def _mock_llm(prompt: str) -> str:
            from tests.helpers.mock_llm import MockLLMComplete
            return MockLLMComplete()(prompt)

        return maint.run_all(db, llm_complete=_mock_llm, emb=emb)

    def sql(self, query: str) -> list[dict]:
        db = self._get_db()
        return db.query(query)

    def health(self) -> dict:
        try:
            db = self._get_db()
            total = db.count()
            return {"status": "ok", "total_memories": total}
        except Exception as e:
            return {"status": "error", "error": str(e)}


class RemoteTransport:
    """HTTP client for accessing a remote ClickMem server over LAN."""

    def __init__(self, base_url: str, api_key: str = ""):
        import httpx
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=60.0,
        )

    def _post(self, path: str, **kwargs) -> dict:
        resp = self._client.post(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str, **kwargs) -> dict | list:
        resp = self._client.get(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> dict:
        resp = self._client.delete(path)
        resp.raise_for_status()
        return resp.json()

    def recall(self, query: str, cfg: RetrievalConfig | None = None,
               min_score: float = 0.0) -> list[dict]:
        body: dict = {"query": query, "min_score": min_score}
        if cfg:
            body["top_k"] = cfg.top_k
            body["layer"] = cfg.layer
            body["category"] = cfg.category
        data = self._post("/v1/recall", json=body)
        return data.get("memories", [])

    def remember(self, content: str, layer: str = "semantic",
                 category: str = "knowledge", tags: list[str] | None = None,
                 no_upsert: bool = False) -> dict:
        body = {
            "content": content, "layer": layer, "category": category,
            "tags": tags or [], "no_upsert": no_upsert,
        }
        return self._post("/v1/remember", json=body)

    def extract(self, text: str, session_id: str = "") -> list[str]:
        data = self._post("/v1/extract", json={"text": text, "session_id": session_id})
        return data.get("ids", [])

    def forget(self, memory_id: str) -> dict:
        return self._delete(f"/v1/forget/{memory_id}")

    def review(self, layer: str = "semantic", limit: int = 100):
        data = self._get("/v1/review", params={"layer": layer, "limit": limit})
        if layer == "working":
            return data.get("content")
        return data.get("memories", [])

    def status(self) -> dict:
        return self._get("/v1/status")

    def maintain(self, dry_run: bool = False) -> dict:
        return self._post("/v1/maintain", json={"dry_run": dry_run})

    def sql(self, query: str) -> list[dict]:
        data = self._post("/v1/sql", json={"query": query})
        return data.get("results", [])

    def health(self) -> dict:
        return self._get("/v1/health")


def get_transport(remote: str | None = None, api_key: str | None = None) -> LocalTransport | RemoteTransport:
    """Factory: return RemoteTransport if a remote URL is given, else LocalTransport."""
    remote = remote or os.environ.get("CLICKMEM_REMOTE")
    api_key = api_key or os.environ.get("CLICKMEM_API_KEY", "")

    if remote:
        if remote == "auto":
            from memory_core.discovery import discover_one
            found = discover_one()
            if found is None:
                raise RuntimeError("No ClickMem server found on LAN via mDNS")
            remote = found
        return RemoteTransport(remote, api_key=api_key)

    return LocalTransport()
