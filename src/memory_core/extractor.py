"""MemoryExtractor — extract memories from conversation messages using an LLM."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from memory_core.models import Memory

if TYPE_CHECKING:
    from memory_core.db import MemoryDB

_EXTRACT_PROMPT_TEMPLATE = """Analyze the following conversation and extract memories.

For each memory, determine:
- content: a concise natural-language description
- layer: "working" (current focus), "episodic" (specific events/decisions), or "semantic" (persistent facts about the user)
- category: one of decision, preference, event, person, project, knowledge, todo, insight
- tags: relevant keyword tags
- entities: names of people, tools, or concepts mentioned

Conversation:
{conversation}

Return a JSON array of extracted memories. Example:
[
  {{"content": "...", "layer": "episodic", "category": "decision", "tags": ["..."], "entities": ["..."]}}
]

Return only the JSON array, no other text."""

_EMERGENCY_PROMPT_TEMPLATE = """This is an emergency context preservation before compaction.
Extract the most important information from the following context as episodic memories.

Context:
{context}

Return a JSON array of memories to preserve. Each must have:
- content: what to remember
- layer: "episodic"
- category: one of decision, preference, event, person, project, knowledge, todo, insight
- tags: relevant keywords
- entities: named entities

Return only the JSON array, no other text."""


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
        prompt = _EXTRACT_PROMPT_TEMPLATE.format(conversation=conversation)
        raw_response = llm_complete(prompt)

        memories = _parse_llm_memories(raw_response)
        ids = []
        for mem_data in memories:
            layer = mem_data.get("layer", "episodic")
            if layer == "working":
                self._db.set_working(mem_data.get("content", ""))
                # get working memory id
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
        prompt = _EMERGENCY_PROMPT_TEMPLATE.format(context=context)
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
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass
    return []
