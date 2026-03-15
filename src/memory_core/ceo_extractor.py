"""CEO Extractor — multi-type extraction from conversation text.

Replaces extractor.py. Extracts episodes, decisions, principles, and
project updates from filtered conversation text using an LLM.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from memory_core.models import Decision, Episode, Principle

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB

logger = logging.getLogger(__name__)

_CEO_EXTRACTION_PROMPT = """\
You are the memory extraction engine for a solo CEO's knowledge system.
Analyse the following conversation and extract structured knowledge items.

Return a JSON array where each item has a "type" field and type-specific fields.
Extract ONLY items that are clearly present — quality over quantity.
If nothing meaningful is found, return an empty array [].

Possible types:

1. **episode** — A summary of what happened in this interaction:
   {{"type": "episode", "content": "...", "user_intent": "...", \
"key_outcomes": ["..."], "domain": "tech|product|design|marketing|ops", \
"tags": ["..."], "entities": ["..."]}}

2. **decision** — A decision that was made or discussed:
   {{"type": "decision", "title": "short title", "context": "why this came up", \
"choice": "what was decided", "reasoning": "why this choice", \
"alternatives": "what else was considered", "domain": "tech|product|design|marketing|ops"}}

3. **principle** — A reusable rule, preference, or guideline revealed:
   {{"type": "principle", "content": "the principle statement", \
"domain": "tech|product|design|marketing|ops|management", "confidence": 0.5-1.0}}
   Only extract principles with confidence >= 0.6. Be conservative.

4. **project_update** — An update to the current project's metadata:
   {{"type": "project_update", "field": "status|vision|target_users|north_star_metric|description", \
"new_value": "..."}}

Rules:
- Prefer fewer, higher-quality extractions over many low-quality ones.
- Do not invent information not present in the conversation.
- Decisions must be actual choices made, not hypothetical discussions.
- Principles must be generalisable beyond this single conversation.
- Return ONLY the JSON array, no other text.

---
CONVERSATION:
{text}
"""


@dataclass
class ExtractionResult:
    """Result of CEO extraction from a conversation."""

    episode_ids: list[str] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    principle_ids: list[str] = field(default_factory=list)
    project_updates: list[dict] = field(default_factory=list)


class CEOExtractor:
    """Extract CEO knowledge entities from conversation text."""

    def __init__(self, ceo_db: CeoDB, emb):
        self._db = ceo_db
        self._emb = emb

    def extract(
        self,
        text: str,
        llm_complete: Callable[[str], str],
        project_id: str = "",
        session_id: str = "",
        agent_source: str = "",
        raw_id: str = "",
    ) -> ExtractionResult:
        """Run multi-type extraction on filtered conversation text."""
        result = ExtractionResult()

        if not text or not text.strip():
            return result

        # Call LLM
        prompt = _CEO_EXTRACTION_PROMPT.format(text=text[:4000])
        try:
            raw_response = llm_complete(prompt)
        except Exception as e:
            logger.warning("CEO extraction LLM call failed: %s", e)
            return result

        # Parse JSON
        items = self._parse_response(raw_response)
        if not items:
            return result

        # Process each item
        for item in items:
            item_type = item.get("type", "")
            try:
                if item_type == "episode":
                    eid = self._process_episode(item, project_id, session_id, agent_source, raw_id)
                    if eid:
                        result.episode_ids.append(eid)
                elif item_type == "decision":
                    did = self._process_decision(item, project_id)
                    if did:
                        result.decision_ids.append(did)
                elif item_type == "principle":
                    pid = self._process_principle(item, project_id)
                    if pid:
                        result.principle_ids.append(pid)
                elif item_type == "project_update":
                    result.project_updates.append(item)
                    if project_id:
                        field_name = item.get("field", "")
                        new_value = item.get("new_value", "")
                        if field_name and new_value:
                            self._db.update_project(project_id, **{field_name: new_value})
            except Exception as e:
                logger.warning("Failed to process extracted item %s: %s", item_type, e)

        return result

    def _process_episode(
        self, item: dict, project_id: str, session_id: str, agent_source: str, raw_id: str,
    ) -> str | None:
        content = item.get("content", "")
        if not content:
            return None
        ep = Episode(
            project_id=project_id,
            session_id=session_id,
            agent_source=agent_source,
            content=content,
            user_intent=item.get("user_intent", ""),
            key_outcomes=item.get("key_outcomes", []),
            domain=item.get("domain", "tech"),
            tags=item.get("tags", []),
            entities=item.get("entities", []),
            raw_id=raw_id,
        )
        if self._emb:
            ep.embedding = self._emb.encode_document(content)
        return self._db.insert_episode(ep)

    def _process_decision(self, item: dict, project_id: str) -> str | None:
        title = item.get("title", "")
        if not title:
            return None
        d = Decision(
            project_id=project_id,
            title=title,
            context=item.get("context", ""),
            choice=item.get("choice", ""),
            reasoning=item.get("reasoning", ""),
            alternatives=item.get("alternatives", ""),
            domain=item.get("domain", "tech"),
        )
        embed_text = f"{title} {d.choice} {d.reasoning}"
        if self._emb:
            d.embedding = self._emb.encode_document(embed_text)
        return self._db.insert_decision(d)

    def _process_principle(self, item: dict, project_id: str) -> str | None:
        content = item.get("content", "")
        confidence = float(item.get("confidence", 0.5))
        if not content or confidence < 0.6:
            return None
        p = Principle(
            project_id=project_id,
            content=content,
            domain=item.get("domain", "tech"),
            confidence=confidence,
            evidence_count=1,
        )
        if self._emb:
            p.embedding = self._emb.encode_document(content)
        return self._db.insert_principle(p)

    @staticmethod
    def _parse_response(raw: str) -> list[dict]:
        """Parse LLM response as JSON array, tolerating markdown fences."""
        text = raw.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            logger.warning("Failed to parse CEO extraction response as JSON")
        return []
