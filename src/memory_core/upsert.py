"""Smart upsert for memory storage — Mem0-style search → LLM judge → execute."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from memory_core.db import MemoryDB
from memory_core.json_utils import extract_json
from memory_core.models import Memory, RetrievalConfig
from memory_core.retrieval import hybrid_search

SIMILARITY_THRESHOLD = 0.5

UPSERT_PROMPT_TEMPLATE = """\
Decide how to handle a new memory given existing similar memories.

New memory: {new_content}

Existing memories:
{formatted_existing}

For each existing memory pick one action: NOOP, UPDATE (provide merged text), or DELETE.
Then decide if the new memory should be added separately (true/false).

Return ONLY a JSON object like:
{{"memory_actions": [{{"existing_id": "abc", "action": "UPDATE", "updated_content": "merged text"}}], "should_add": false}}
"""


@dataclass
class UpsertResult:
    """Result of an upsert operation."""

    added_id: Optional[str] = None
    updated: list[dict] = field(default_factory=list)  # [{"id": ..., "new_content": ...}]
    deleted: list[str] = field(default_factory=list)
    action: str = "ADD"  # ADD | UPDATE | NOOP | FALLBACK_ADD

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "added_id": self.added_id,
            "updated": self.updated,
            "deleted": self.deleted,
        }


def _format_existing(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [ID: {r['id']}] {r['content']}")
    return "\n".join(lines)


def _parse_llm_response(response: str) -> Optional[dict]:
    """Parse LLM JSON response with robust extraction."""
    return extract_json(response, expect="object")


def upsert(
    db: MemoryDB,
    emb,
    content: str,
    layer: str,
    category: str,
    tags: list[str],
    llm_complete: Optional[Callable[[str], str]] = None,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> UpsertResult:
    """Smart upsert: search for similar memories, use LLM to decide actions.

    Flow:
    1. episodic layer → direct INSERT (no dedup)
    2. Embed + hybrid search for similar memories
    3. No similar results → INSERT
    4. Similar results + LLM → LLM decides UPDATE/DELETE/ADD
    5. Similar results + no LLM → fallback INSERT
    """
    # Episodic memories are raw logs — never deduplicate
    if layer == "episodic":
        m = Memory(
            content=content, layer=layer, category=category, tags=tags,
            embedding=emb.encode_document(content), source="cli",
        )
        db.insert(m)
        return UpsertResult(added_id=m.id, action="ADD")

    # Search for similar existing memories
    cfg = RetrievalConfig(top_k=5, layer=layer)
    results = hybrid_search(db, emb, content, cfg=cfg)
    similar = [r for r in results if r.get("final_score", 0) > similarity_threshold]

    # No similar results → direct INSERT
    if not similar:
        m = Memory(
            content=content, layer=layer, category=category, tags=tags,
            embedding=emb.encode_document(content), source="cli",
        )
        db.insert(m)
        return UpsertResult(added_id=m.id, action="ADD")

    # Similar results but no LLM → fallback to INSERT
    if llm_complete is None:
        m = Memory(
            content=content, layer=layer, category=category, tags=tags,
            embedding=emb.encode_document(content), source="cli",
        )
        db.insert(m)
        return UpsertResult(added_id=m.id, action="FALLBACK_ADD")

    # Ask LLM to decide
    prompt = UPSERT_PROMPT_TEMPLATE.format(
        new_content=content,
        formatted_existing=_format_existing(similar),
    )
    raw_response = llm_complete(prompt)
    parsed = _parse_llm_response(raw_response)

    if parsed is None:
        # LLM returned unparseable response → fallback INSERT
        m = Memory(
            content=content, layer=layer, category=category, tags=tags,
            embedding=emb.encode_document(content), source="cli",
        )
        db.insert(m)
        return UpsertResult(added_id=m.id, action="FALLBACK_ADD")

    result = UpsertResult()
    actions = parsed.get("memory_actions", [])
    should_add = parsed.get("should_add", True)

    for action_item in actions:
        existing_id = action_item.get("existing_id", "")
        action = action_item.get("action", "NOOP").upper()
        updated_content = action_item.get("updated_content", "")

        if action == "UPDATE" and updated_content:
            # Update existing memory content and re-embed
            new_id = db.update_content(existing_id, updated_content)
            # Re-embed the updated content
            new_emb = emb.encode_document(updated_content)
            # update_content creates a new version; update its embedding
            _update_embedding(db, new_id, new_emb)
            result.updated.append({"id": existing_id, "new_id": new_id, "new_content": updated_content})
        elif action == "DELETE":
            db.deactivate(existing_id)
            result.deleted.append(existing_id)

    if should_add:
        m = Memory(
            content=content, layer=layer, category=category, tags=tags,
            embedding=emb.encode_document(content), source="cli",
        )
        db.insert(m)
        result.added_id = m.id
        result.action = "ADD" if not result.updated and not result.deleted else "UPSERT"
    else:
        result.action = "MERGED" if result.updated else "NOOP"

    return result


def _update_embedding(db: MemoryDB, memory_id: str, embedding: list[float]) -> None:
    """Update the embedding of a memory in-place."""
    emb_literal = db._float_array_literal(embedding)
    now = db._now_str()
    db._session.query(
        f"ALTER TABLE memories UPDATE embedding = {emb_literal}, updated_at = '{now}' "
        f"WHERE id = '{db._escape(memory_id)}'"
    )
    db._session.query("OPTIMIZE TABLE memories FINAL")
