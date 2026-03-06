"""MemoryExtractor — extract memories from conversation messages using an LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

from memory_core.json_utils import extract_json_or
from memory_core.models import Memory

if TYPE_CHECKING:
    from memory_core.db import MemoryDB

_EXTRACT_PROMPT = """\
Extract important facts from this conversation as structured memories.

Conversation:
{conversation}

For each memory choose:
- layer: "working" (current focus), "episodic" (event/decision), or "semantic" (durable fact)
- category: decision | preference | event | person | project | knowledge | todo | insight

Return ONLY a JSON array. Example:
[{{"content": "Team decided to use PostgreSQL", "layer": "episodic", "category": "decision", "tags": ["database"], "entities": ["PostgreSQL"]}}]
"""

_EMERGENCY_PROMPT = """\
This is an emergency context preservation before compaction.
Extract the most important information from the following context as episodic memories.

Context:
{context}

Return ONLY a JSON array. Each item must have: content, layer (use "episodic"), category, tags, entities.
Example:
[{{"content": "Debugging vector search — cosine scores too low", "layer": "episodic", "category": "event", "tags": ["debugging"], "entities": []}}]
"""


class MemoryExtractor:
    """Extract memories from conversation messages using an LLM."""

    def __init__(self, db: "MemoryDB", emb):
        self._db = db
        self._emb = emb

    def extract(
        self,
        messages: list[dict],
        llm_complete,
        session_id: str = "",
    ) -> list[str]:
        if not messages:
            return []

        conversation = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages
        )
        prompt = _EXTRACT_PROMPT.format(conversation=conversation)
        raw_response = llm_complete(prompt)

        memories = _parse_llm_memories(raw_response)
        ids = []
        for mem_data in memories:
            layer = mem_data.get("layer", "episodic")
            if layer == "working":
                self._db.set_working(mem_data.get("content", ""))
                rows = self._db.list_by_layer("working", limit=1)
                if rows:
                    ids.append(rows[0].id)
                continue

            m = Memory(
                content=mem_data.get("content", ""),
                layer=layer,
                category=mem_data.get("category", "event"),
                tags=mem_data.get("tags", []),
                entities=mem_data.get("entities", []),
                embedding=self._emb.encode_document(mem_data.get("content", "")),
                session_id=session_id,
                source="agent",
            )
            self._db.insert(m)
            ids.append(m.id)

        return ids

    def emergency_flush(self, context: str, llm_complete) -> list[str]:
        prompt = _EMERGENCY_PROMPT.format(context=context)
        raw_response = llm_complete(prompt)

        memories = _parse_llm_memories(raw_response)
        ids = []
        for mem_data in memories:
            m = Memory(
                content=mem_data.get("content", ""),
                layer="episodic",
                category=mem_data.get("category", "event"),
                tags=mem_data.get("tags", []),
                entities=mem_data.get("entities", []),
                embedding=self._emb.encode_document(mem_data.get("content", "")),
                source="compaction_flush",
            )
            self._db.insert(m)
            ids.append(m.id)

        return ids


def _parse_llm_memories(raw: str) -> list[dict]:
    """Parse LLM response into a list of memory dicts."""
    result = extract_json_or(raw, [], expect="array")
    if isinstance(result, dict):
        return [result]
    return result
