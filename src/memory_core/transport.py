"""Transport abstraction — local or remote access to ClickMem memory operations.

LocalTransport: direct in-process calls to memory_core (default, existing behavior).
RemoteTransport: HTTP calls to a ClickMem REST API server (for LAN/remote access).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional, Protocol

from memory_core.models import Memory, RetrievalConfig

_log = logging.getLogger("clickmem.transport")


class Transport(Protocol):
    """Protocol defining the memory operation interface."""

    def recall(self, query: str, cfg: RetrievalConfig | None = None,
               min_score: float = 0.0) -> list[dict]: ...

    def remember(self, content: str, layer: str = "semantic",
                 category: str = "knowledge", tags: list[str] | None = None,
                 no_upsert: bool = False) -> dict: ...

    def extract(self, text: str, session_id: str = "") -> list[str]: ...

    def ingest(self, text: str, session_id: str = "",
               source: str = "cursor", cwd: str = "") -> dict: ...

    def forget(self, memory_id: str) -> dict: ...

    def review(self, layer: str = "semantic", limit: int = 100) -> list[Memory] | str | None: ...

    def list_memories(self, *, layer: str | None = None, category: str | None = None,
                      limit: int = 50, offset: int = 0, sort_by: str = "created_at",
                      since: str | None = None, until: str | None = None) -> list: ...

    def status(self) -> dict: ...

    def maintain(self, dry_run: bool = False) -> dict: ...

    def sql(self, query: str) -> list[dict]: ...

    def health(self) -> dict: ...


class LocalTransport:
    """Direct in-process access — current behavior, no network overhead."""

    _REFINE_THRESHOLD = int(os.environ.get("CLICKMEM_REFINE_THRESHOLD", "1"))

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.environ.get(
            "CLICKMEM_DB_PATH",
            os.path.expanduser("~/.openclaw/memory/chdb-data"),
        )
        self._db = None
        self._ceo_db = None
        self._emb = None
        self._refinement_lock = threading.Lock()

    def _get_db(self):
        if self._db is None:
            from memory_core.db import MemoryDB
            self._db = MemoryDB(self._db_path)
        return self._db

    def _get_ceo_db(self):
        if self._ceo_db is None:
            from memory_core.ceo_db import CeoDB
            self._ceo_db = CeoDB(self._db_path)
        return self._ceo_db

    def _get_emb(self):
        if self._emb is None:
            from memory_core.embedding import EmbeddingEngine
            self._emb = EmbeddingEngine()
            self._emb.load()
        return self._emb

    def recall(self, query: str, cfg: RetrievalConfig | None = None,
               min_score: float = 0.0, cwd: str = "") -> list[dict]:
        from memory_core.retrieval import hybrid_search
        from memory_core.ceo_retrieval import ceo_search

        db = self._get_db()
        emb = self._get_emb()
        top_k = cfg.top_k if cfg else 10

        old_results = hybrid_search(db, emb, query, cfg=cfg)
        for r in old_results:
            try:
                db.touch(r["id"])
            except Exception:
                pass

        try:
            ceo_db = self._get_ceo_db()
            # Detect current project for scope-aware scoring
            current_project_id = None
            if cwd:
                from memory_core.project_detect import detect_project
                current_project_id = detect_project(ceo_db, cwd=cwd, emb=emb) or None
            ceo_results = ceo_search(
                ceo_db, emb, query, project_id=current_project_id, top_k=top_k,
            )
            _CEO_LAYER_MAP = {"decision": "semantic", "principle": "semantic", "episode": "episodic"}
            for r in ceo_results:
                r.setdefault("final_score", r.get("score", 0))
                r.setdefault("source", "ceo")
                r.setdefault("layer", _CEO_LAYER_MAP.get(r.get("entity_type", ""), "semantic"))
        except Exception:
            ceo_results = []

        combined = old_results + ceo_results
        if min_score > 0:
            combined = [r for r in combined if r.get("final_score", 0) >= min_score]
        combined.sort(key=lambda r: r.get("final_score", 0), reverse=True)
        return combined[:top_k]

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
            from memory_core.upsert import _strip_injected_context
            if layer == "episodic":
                content = _strip_injected_context(content)
                if not content:
                    return {"layer": layer, "status": "skipped", "reason": "empty after context strip"}
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
            if "[object Object]" in text:
                return []
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

    _GARBAGE_PATTERN_THRESHOLD = 1

    def ingest(self, text: str, session_id: str = "",
               source: str = "cursor", cwd: str = "") -> dict:
        """CEO Brain ingestion pipeline.

        1. Filter conversation (user-input-first)
        2. Store raw transcript
        3. Detect project
        4. CEO multi-type extraction
        5. Dedup & merge
        """
        if text.count("[object Object]") >= self._GARBAGE_PATTERN_THRESHOLD:
            _log.warning("Rejecting ingest: text contains %d '[object Object]' — likely serialization bug in client",
                         text.count("[object Object]"))
            return {"error": "rejected", "reason": "text contains unserialized JS objects"}

        db = self._get_db()
        ceo_db = self._get_ceo_db()
        emb = self._get_emb()

        # Step 1: Filter conversation
        from memory_core.conversation_filter import filter_conversation
        filtered = filter_conversation([{"role": "user", "content": text}], agent_source=source)

        # Step 2: Store raw transcript
        raw_id = db.insert_raw(session_id, source, text)

        # Step 3: Detect project (auto-create from cwd if no match)
        from memory_core.project_detect import detect_project
        project_id = detect_project(
            ceo_db, cwd=cwd, content=filtered, emb=emb,
            allow_auto_create=True,
        )

        # Step 4: CEO extraction
        from memory_core.llm import get_llm_complete
        llm_complete = get_llm_complete()

        if llm_complete is None:
            # Fallback: store as raw episode
            from memory_core.models import Episode
            ep = Episode(
                content=filtered, project_id=project_id,
                session_id=session_id, agent_source=source,
                raw_id=raw_id,
                embedding=emb.encode_document(filtered),
            )
            ceo_db.insert_episode(ep)
            db.mark_raw_processed(raw_id)
            return {"raw_id": raw_id, "episodes": [ep.id]}

        from memory_core.ceo_extractor import CEOExtractor
        extractor = CEOExtractor(ceo_db, emb)
        result = extractor.extract(
            filtered, llm_complete, project_id=project_id,
            session_id=session_id, agent_source=source, raw_id=raw_id,
        )

        db.mark_raw_processed(raw_id)

        return {
            "raw_id": raw_id,
            "episodes": result.episode_ids,
            "decisions": result.decision_ids,
            "principles": result.principle_ids,
            "project_updates": result.project_updates,
        }

    def _trigger_refinement(self) -> None:
        """Start refinement in a background thread if not already running."""
        if not self._refinement_lock.acquire(blocking=False):
            return
        t = threading.Thread(target=self._run_refinement, daemon=True)
        t.start()

    def _run_refinement(self) -> None:
        try:
            from memory_core.refinement import ContinualRefinement
            from memory_core.llm import get_llm_complete
            db = self._get_db()
            emb = self._get_emb()
            llm = get_llm_complete()
            if llm is None:
                return
            # Phase 1: drain unprocessed backlog (extraction only, no heavy refinement)
            while True:
                remaining = db.count_raw().get("unprocessed", 0)
                if remaining == 0:
                    break
                _log.info("Refinement: extracting %d unprocessed raw transcripts...", remaining)
                ContinualRefinement._dedup_exact_text(db)
                extracted = ContinualRefinement._reextract_unprocessed(db, emb, llm)
                _log.info("Refinement: extracted %d memories from batch", extracted)
            # Phase 2: one full refinement pass (cluster/merge/prune)
            _log.info("Refinement: backlog cleared, running full pipeline")
            ContinualRefinement.run(db, emb, llm)
        except Exception as exc:
            _log.warning("Background refinement failed: %s", exc)
        finally:
            self._refinement_lock.release()

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
                    f"SELECT id FROM memories FINAL "
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

    def list_memories(self, *, layer: str | None = None, category: str | None = None,
                      limit: int = 50, offset: int = 0, sort_by: str = "created_at",
                      since: str | None = None, until: str | None = None) -> list[Memory]:
        db = self._get_db()
        return db.list_memories(
            layer=layer, category=category, limit=limit, offset=offset,
            sort_by=sort_by, since=since, until=until,
        )

    def status(self) -> dict:
        db = self._get_db()
        counts = db.count_by_layer()
        total = db.count()
        breakdown = db.stats()
        raw_counts = db.count_raw()
        return {
            "counts": counts, "total": total,
            "breakdown": breakdown, "raw": raw_counts,
        }

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
        from memory_core.llm import get_llm_complete
        emb = self._get_emb()
        llm = get_llm_complete()

        old_result = maint.run_all(db, llm_complete=llm, emb=emb)

        try:
            from memory_core.ceo_maintenance import CEOMaintenance
            ceo_db = self._get_ceo_db()
            ceo_result = CEOMaintenance.run_all(ceo_db, llm_complete=llm, emb=emb)
            old_result.update(ceo_result)
        except Exception as exc:
            _log.warning("CEO maintenance failed: %s", exc)

        return old_result

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

    def ingest(self, text: str, session_id: str = "",
               source: str = "cursor", cwd: str = "") -> dict:
        body: dict = {"text": text, "session_id": session_id, "source": source}
        if cwd:
            body["cwd"] = cwd
        return self._post("/v1/ingest", json=body)

    def forget(self, memory_id: str) -> dict:
        return self._delete(f"/v1/forget/{memory_id}")

    def review(self, layer: str = "semantic", limit: int = 100):
        data = self._get("/v1/review", params={"layer": layer, "limit": limit})
        if layer == "working":
            return data.get("content")
        return data.get("memories", [])

    def list_memories(self, *, layer: str | None = None, category: str | None = None,
                      limit: int = 50, offset: int = 0, sort_by: str = "created_at",
                      since: str | None = None, until: str | None = None) -> list:
        params = {"limit": limit, "offset": offset, "sort_by": sort_by}
        if layer:
            params["layer"] = layer
        if category:
            params["category"] = category
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        data = self._get("/v1/list", params=params)
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


_SERVER_PROBE_TIMEOUT = 2.0


def _localhost_url() -> str:
    host = os.environ.get("CLICKMEM_SERVER_HOST", "127.0.0.1")
    port = os.environ.get("CLICKMEM_SERVER_PORT", "9527")
    return f"http://{host}:{port}"


def get_transport(remote: str | None = None, api_key: str | None = None) -> LocalTransport | RemoteTransport:
    """Factory for **client** use (CLI, plugins) — always returns RemoteTransport.

    Server processes (``clickmem-mcp``, ``memory serve``) create
    ``LocalTransport`` directly; this function is for code that connects
    to a running API server.

    Priority:
      1. Explicit ``remote`` / ``CLICKMEM_REMOTE`` env → RemoteTransport
      2. Local server (CLICKMEM_SERVER_HOST:CLICKMEM_SERVER_PORT, quick probe) → RemoteTransport
      3. Error — no server available
    """
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

    # Probe local server
    local_url = _localhost_url()
    try:
        import httpx
        with httpx.Client(base_url=local_url, timeout=_SERVER_PROBE_TIMEOUT) as probe:
            resp = probe.get("/v1/health")
            resp.raise_for_status()
        return RemoteTransport(local_url, api_key=api_key)
    except Exception:
        raise RuntimeError(
            f"No ClickMem API server at {local_url}. "
            "The server starts automatically with Cursor/Claude Code (clickmem-mcp) "
            "or manually via 'memory serve'."
        )
