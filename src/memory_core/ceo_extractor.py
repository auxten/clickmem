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


def _ensure_str(val, sep="; "):
    """Coerce LLM output to str — lists are joined, None becomes ''."""
    if isinstance(val, list):
        return sep.join(str(x) for x in val)
    if not isinstance(val, str):
        return str(val) if val is not None else ""
    return val

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
"alternatives": "what else was considered", "domain": "tech|product|design|marketing|ops", \
"activation_scope": ["scope1", "scope2"]}}

3. **principle** — A reusable rule, preference, or guideline revealed:
   {{"type": "principle", "content": "the principle statement", \
"domain": "tech|product|design|marketing|ops|management", "confidence": 0.5-1.0, \
"activation_scope": ["scope1", "scope2"]}}
   Only extract principles with confidence >= 0.7. Be very conservative.

4. **project_update** — An update to the current project's metadata:
   {{"type": "project_update", "field": "status|vision|target_users|north_star_metric|description", \
"new_value": "..."}}

5. **project_hint** — If the conversation clearly discusses a specific project \
different from what the working directory suggests:
   {{"type": "project_hint", "project_name": "the actual project being discussed"}}

Rules:
- Prefer fewer, higher-quality extractions over many low-quality ones.
- Do not invent information not present in the conversation.
- Decisions must be actual choices made, not hypothetical discussions.
- If the conversation contains role-based workflow outputs (JSON with APPROVE/REJECT/IMPROVE), \
extract the underlying product or tech decision, not the approval action itself. \
The real decision is what was approved and why — extract that substance.
- Each episode should capture WHAT happened and WHY, not just list actions. \
Prioritise episodes that show user intent, unexpected outcomes, or pivots.
- Principles must pass the "would I tell this to a new team member" test. \
Do NOT extract: basic language syntax everyone knows, common best practices, \
one-time operational instructions, or project-specific URLs/paths. \
A good principle is a LESSON LEARNED from experience, not a textbook fact. \
Extract at most 2 principles per conversation.
- activation_scope describes the TYPE of task where this applies.
  Examples: ["产品功能设计", "API design"], ["debugging", "performance tuning"].
  Leave empty [] if universal.
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
        """Run multi-type extraction on filtered conversation text.

        For long texts, splits into segments and extracts from each.
        Dedup in _process_* methods prevents duplicates across segments.
        """
        result = ExtractionResult()

        if not text or not text.strip():
            return result

        from memory_core.conversation_filter import segment_conversation
        segments = segment_conversation(text)

        for seg in segments:
            self._extract_segment(
                seg, llm_complete, result,
                project_id=project_id, session_id=session_id,
                agent_source=agent_source, raw_id=raw_id,
            )

        return result

    def _extract_segment(
        self,
        text: str,
        llm_complete: Callable[[str], str],
        result: ExtractionResult,
        project_id: str = "",
        session_id: str = "",
        agent_source: str = "",
        raw_id: str = "",
    ) -> None:
        """Extract from a single text segment, appending to result."""
        prompt = _CEO_EXTRACTION_PROMPT.format(text=text[:4000])
        try:
            raw_response = llm_complete(prompt)
        except Exception as e:
            logger.warning("CEO extraction LLM call failed: %s", e)
            return

        items = self._parse_response(raw_response)
        if not items:
            return

        # Check for project_hint — override project_id if LLM detects
        # the conversation is about a different project than the CWD.
        effective_project_id = project_id
        for item in items:
            if isinstance(item, dict) and item.get("type") == "project_hint":
                hint_name = item.get("project_name", "")
                if hint_name:
                    projects = self._db.list_projects()
                    for p in projects:
                        if p.name and p.name.lower() == hint_name.lower():
                            logger.info(
                                "project_hint: overriding project_id from %s to %s (%s)",
                                project_id[:8] if project_id else "none",
                                p.id[:8], p.name,
                            )
                            effective_project_id = p.id
                            break

        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            try:
                if item_type == "episode":
                    eid = self._process_episode(item, effective_project_id, session_id, agent_source, raw_id)
                    if eid:
                        result.episode_ids.append(eid)
                elif item_type == "decision":
                    did = self._process_decision(item, effective_project_id)
                    if did:
                        result.decision_ids.append(did)
                elif item_type == "principle":
                    pid = self._process_principle(item, effective_project_id)
                    if pid:
                        result.principle_ids.append(pid)
                elif item_type == "project_update":
                    result.project_updates.append(item)
                    if effective_project_id:
                        field_name = item.get("field", "")
                        new_value = item.get("new_value", "")
                        if field_name and new_value:
                            self._db.update_project(effective_project_id, **{field_name: new_value})
                # project_hint already handled above
            except Exception as e:
                logger.warning("Failed to process extracted item %s: %s", item_type, e)

    _TRIVIAL_CHOICES = frozenset({"APPROVE", "REJECT", "IMPROVE", "PASS", "FAIL"})

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

        from memory_core.ceo_dedup import dedup_episode
        dup_id = dedup_episode(self._db, self._emb, ep)
        if dup_id:
            logger.debug("Episode dedup: skipping duplicate of %s", dup_id[:8])
            return None

        return self._db.insert_episode(ep)

    def _process_decision(self, item: dict, project_id: str) -> str | None:
        title = item.get("title", "")
        choice = item.get("choice", "")
        if not title:
            return None
        if choice.strip().upper() in self._TRIVIAL_CHOICES:
            return None

        activation_scope = item.get("activation_scope", [])
        scope_embedding = None
        if activation_scope and self._emb:
            from memory_core.ceo_skills import _compute_scope_embedding
            scope_embedding = _compute_scope_embedding(self._emb, activation_scope)

        d = Decision(
            project_id=project_id,
            title=title,
            context=_ensure_str(item.get("context", "")),
            choice=choice,
            reasoning=_ensure_str(item.get("reasoning", "")),
            alternatives=_ensure_str(item.get("alternatives", "")),
            domain=_ensure_str(item.get("domain", "tech")),
            activation_scope=activation_scope,
            scope_embedding=scope_embedding,
        )
        embed_text = f"{title} {choice} {d.reasoning}"
        if self._emb:
            d.embedding = self._emb.encode_document(embed_text)

        from memory_core.ceo_dedup import dedup_decision
        result = dedup_decision(self._db, self._emb, d)
        if result.action == "UPDATE" and result.existing_id:
            self._db.update_decision(result.existing_id,
                                     context=d.context, choice=d.choice,
                                     reasoning=d.reasoning, alternatives=d.alternatives)
            return result.existing_id
        if result.action == "NOOP":
            return None

        return self._db.insert_decision(d)

    def _process_principle(self, item: dict, project_id: str) -> str | None:
        content = item.get("content", "")
        confidence = float(item.get("confidence", 0.5))
        if not content or confidence < 0.7:
            return None

        activation_scope = item.get("activation_scope", [])
        scope_embedding = None
        if activation_scope and self._emb:
            from memory_core.ceo_skills import _compute_scope_embedding
            scope_embedding = _compute_scope_embedding(self._emb, activation_scope)

        p = Principle(
            project_id=project_id,
            content=content,
            domain=item.get("domain", "tech"),
            confidence=confidence,
            evidence_count=1,
            activation_scope=activation_scope,
            scope_embedding=scope_embedding,
        )
        if self._emb:
            p.embedding = self._emb.encode_document(content)

        from memory_core.ceo_dedup import dedup_principle
        result = dedup_principle(self._db, self._emb, p)
        if result.action in ("NOOP", "CONFLICT"):
            if result.action == "CONFLICT":
                logger.warning("Principle conflict with %s: %s", result.existing_id, result.note)
            return None

        return self._db.insert_principle(p)

    @staticmethod
    def _parse_response(raw: str) -> list[dict]:
        """Parse LLM response as JSON array, tolerating markdown fences and trailing text.

        Local models often append chain-of-thought after the JSON.  We bracket-match
        to extract just the outermost ``[…]`` or ``{…}`` structure.
        """
        text = raw.strip()
        # Strip markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Fast path: entire text is valid JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            pass

        # Extract first JSON array via bracket counting
        start = text.find("[")
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(text)):
                ch = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, list):
                                return parsed
                        except json.JSONDecodeError:
                            break
                        break

        # Fallback: try to find a JSON object
        start = text.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(text)):
                ch = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, dict):
                                return [parsed]
                        except json.JSONDecodeError:
                            break
                        break

        logger.warning("Failed to parse CEO extraction response as JSON: %.200s", text)
        return []
