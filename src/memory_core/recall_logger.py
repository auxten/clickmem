"""Structured JSONL logger for recall queries.

Gated by CLICKMEM_RECALL_LOG=1. Writes to ~/.openclaw/memory/recall.jsonl.
Auto-rotates when file exceeds 10MB.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_ENABLED = os.environ.get("CLICKMEM_RECALL_LOG", "0") == "1"
_MAX_SIZE = 10 * 1024 * 1024  # 10MB


def _log_path() -> Path:
    base = os.environ.get("CLICKMEM_DB_PATH", "")
    if base and base != ":memory:":
        return Path(base).parent / "recall.jsonl"
    return Path.home() / ".openclaw" / "memory" / "recall.jsonl"


def log_recall(
    query: str,
    project_id: str,
    session_id: str,
    results: list[dict],
    latency_ms: float,
) -> None:
    """Append a recall entry to the JSONL log. No-op if disabled."""
    if not _ENABLED:
        return

    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        # Auto-rotate
        if path.exists() and path.stat().st_size > _MAX_SIZE:
            rotated = path.with_suffix(".jsonl.1")
            if rotated.exists():
                rotated.unlink()
            path.rename(rotated)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": query[:200],
            "project_id": project_id,
            "session_id": session_id,
            "result_count": len(results),
            "top_score": results[0]["score"] if results else 0.0,
            "latency_ms": round(latency_ms, 1),
            "top_results": [
                {"id": r["id"][:12], "type": r["entity_type"], "score": round(r["score"], 4)}
                for r in results[:5]
            ],
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Failed to write recall log: %s", e)
