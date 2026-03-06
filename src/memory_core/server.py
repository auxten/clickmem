"""ClickMem REST API Server — FastAPI-based HTTP service for LAN memory sharing.

Start with: memory serve --host 0.0.0.0 --port 9527
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Header, Request
from pydantic import BaseModel, Field

from memory_core.auth import verify_api_key

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RecallRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)
    layer: Optional[str] = None
    category: Optional[str] = None


class RememberRequest(BaseModel):
    content: str
    layer: str = "semantic"
    category: str = "knowledge"
    tags: list[str] = Field(default_factory=list)
    no_upsert: bool = False


class ExtractRequest(BaseModel):
    text: str
    session_id: str = ""


class MaintainRequest(BaseModel):
    dry_run: bool = False


class SqlRequest(BaseModel):
    query: str


# ---------------------------------------------------------------------------
# App lifecycle — load heavy resources once
# ---------------------------------------------------------------------------

_transport = None


def _get_transport():
    global _transport
    if _transport is None:
        from memory_core.transport import LocalTransport
        _transport = LocalTransport()
    return _transport


@asynccontextmanager
async def lifespan(application: FastAPI):
    _get_transport()
    yield


app = FastAPI(
    title="ClickMem",
    description="Unified memory center for AI coding agents",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

_api_key_env: str | None = None


def _get_expected_key() -> str:
    global _api_key_env
    if _api_key_env is None:
        _api_key_env = os.environ.get("CLICKMEM_API_KEY", "")
    return _api_key_env


async def auth_dep(authorization: Optional[str] = Header(None)):
    expected = _get_expected_key()
    if not expected:
        return
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not verify_api_key(token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Debug-mode guard for SQL endpoint
# ---------------------------------------------------------------------------

_debug_mode = False


def set_debug_mode(enabled: bool):
    global _debug_mode
    _debug_mode = enabled


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/v1/health")
async def health():
    t = _get_transport()
    return t.health()


@app.post("/v1/recall", dependencies=[Depends(auth_dep)])
async def recall(req: RecallRequest):
    from memory_core.models import RetrievalConfig
    t = _get_transport()
    cfg = RetrievalConfig(
        top_k=req.top_k,
        layer=req.layer,
        category=req.category,
    )
    results = t.recall(req.query, cfg=cfg, min_score=req.min_score)
    return {"memories": results}


@app.post("/v1/remember", dependencies=[Depends(auth_dep)])
async def remember(req: RememberRequest):
    t = _get_transport()
    return t.remember(
        content=req.content, layer=req.layer,
        category=req.category, tags=req.tags,
        no_upsert=req.no_upsert,
    )


@app.post("/v1/extract", dependencies=[Depends(auth_dep)])
async def extract(req: ExtractRequest):
    t = _get_transport()
    ids = t.extract(text=req.text, session_id=req.session_id)
    return {"ids": ids}


@app.delete("/v1/forget/{memory_id}", dependencies=[Depends(auth_dep)])
async def forget(memory_id: str):
    t = _get_transport()
    result = t.forget(memory_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/v1/review", dependencies=[Depends(auth_dep)])
async def review(layer: str = "semantic", limit: int = 100):
    t = _get_transport()
    data = t.review(layer=layer, limit=limit)
    if layer == "working":
        return {"layer": "working", "content": data}
    memories = []
    if isinstance(data, list):
        for m in data:
            if hasattr(m, "content"):
                memories.append({
                    "id": m.id, "layer": m.layer, "category": m.category,
                    "content": m.content, "tags": m.tags,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                })
            else:
                memories.append(m)
    return {"layer": layer, "memories": memories}


@app.get("/v1/status", dependencies=[Depends(auth_dep)])
async def status():
    t = _get_transport()
    return t.status()


@app.post("/v1/maintain", dependencies=[Depends(auth_dep)])
async def maintain(req: MaintainRequest):
    t = _get_transport()
    return t.maintain(dry_run=req.dry_run)


@app.post("/v1/sql", dependencies=[Depends(auth_dep)])
async def sql(req: SqlRequest):
    if not _debug_mode:
        raise HTTPException(status_code=403, detail="SQL endpoint requires --debug mode")
    t = _get_transport()
    try:
        results = t.sql(req.query)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def run_server(host: str = "127.0.0.1", port: int = 9527, debug: bool = False,
               register_mdns: bool = True):
    """Start the ClickMem server (blocking)."""
    import uvicorn
    set_debug_mode(debug)

    mdns_cleanup = None
    if register_mdns and host in ("0.0.0.0", "::"):
        try:
            from memory_core.discovery import register_service, get_local_ip
            local_ip = get_local_ip()
            mdns_cleanup = register_service(local_ip, port)
            print(f"mDNS: registered clickmem at {local_ip}:{port}")
        except Exception as e:
            print(f"mDNS registration skipped: {e}")

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        if mdns_cleanup:
            mdns_cleanup()
