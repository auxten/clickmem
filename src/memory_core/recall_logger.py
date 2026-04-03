"""Structured JSONL logger for recall queries and system events.

Always-on logging for post-hoc analysis and debugging.
Writes to ~/.openclaw/memory/logs/recall.jsonl (auto-rotates at 10MB).

Log entry types:
- "recall": every recall query with results, keywords, timing
- "ingest": extraction events from session transcripts
- "error": errors during recall/ingest pipeline
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_SIZE = 10 * 1024 * 1024  # 10MB


def _log_dir() -> Path:
    base = os.environ.get("CLICKMEM_DB_PATH", "")
    if base and base != ":memory:":
        return Path(base).parent / "logs"
    return Path.home() / ".openclaw" / "memory" / "logs"


def _write_entry(filename: str, entry: dict) -> None:
    """Append a JSONL entry with auto-rotation."""
    try:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / filename

        # Auto-rotate
        if path.exists() and path.stat().st_size > _MAX_SIZE:
            rotated = path.with_suffix(".jsonl.1")
            if rotated.exists():
                rotated.unlink()
            path.rename(rotated)

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Failed to write log %s: %s", filename, e)


def log_recall(
    query: str,
    project_id: str,
    session_id: str,
    results: list[dict],
    latency_ms: float,
    keywords: list[str] | None = None,
    expanded_terms: list[str] | None = None,
    named_entities: list[str] | None = None,
) -> None:
    """Log every recall query with full results for post-hoc analysis."""
    entry = {
        "type": "recall",
        "ts": datetime.now(timezone.utc).isoformat(),
        "query": query[:500],
        "project_id": project_id,
        "session_id": session_id,
        "keywords": keywords or [],
        "expanded_terms": expanded_terms or [],
        "named_entities": named_entities or [],
        "result_count": len(results),
        "top_score": round(results[0]["score"], 4) if results else 0.0,
        "latency_ms": round(latency_ms, 1),
        "results": [
            {
                "id": r.get("id", "")[:16],
                "entity_type": r.get("entity_type", r.get("layer", "")),
                "score": round(r.get("score", 0), 4),
                "content": r.get("content", "")[:200],
            }
            for r in results[:10]
        ],
    }
    _write_entry("recall.jsonl", entry)


def log_ingest(
    session_id: str,
    source: str,
    extracted: dict,
    latency_ms: float = 0.0,
) -> None:
    """Log extraction/ingest events from session transcripts."""
    entry = {
        "type": "ingest",
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "source": source,
        "latency_ms": round(latency_ms, 1),
        "extracted": {
            k: len(v) if isinstance(v, list) else v
            for k, v in extracted.items()
        },
    }
    _write_entry("ingest.jsonl", entry)


def log_error(
    operation: str,
    error: str,
    context: dict | None = None,
) -> None:
    """Log errors in the recall/ingest pipeline."""
    entry = {
        "type": "error",
        "ts": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "error": error[:500],
        "context": context or {},
    }
    _write_entry("error.jsonl", entry)
