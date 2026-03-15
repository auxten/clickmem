"""CEO Dedup — deduplication and merge for CEO Brain entities.

Replaces upsert.py. Checks for near-duplicate episodes, decisions, and
principles before inserting new ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from memory_core.models import Decision, Episode, Principle

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB


@dataclass
class DedupResult:
    """Result of a deduplication check."""

    action: str  # ADD | UPDATE | NOOP | CONFLICT
    existing_id: str | None = None
    note: str = ""


def dedup_episode(
    ceo_db: CeoDB,
    emb,
    episode: Episode,
    threshold: float = 0.95,
) -> str | None:
    """Check if a near-identical episode exists. Returns existing ID if dup, None if new."""
    if not episode.embedding or not episode.content:
        return None

    results = ceo_db.search_episodes_by_vector(
        episode.embedding,
        project_id=episode.project_id or None,
        limit=3,
    )

    for existing in results:
        if not existing.embedding:
            continue
        dist = ceo_db._cosine_dist(episode.embedding, existing.embedding)
        similarity = 1.0 - dist
        if similarity >= threshold:
            return existing.id

    return None


def dedup_decision(
    ceo_db: CeoDB,
    emb,
    decision: Decision,
    llm_complete: Callable[[str], str] | None = None,
) -> DedupResult:
    """Check for similar decisions. If LLM available, ask it to judge."""
    if not decision.embedding:
        return DedupResult(action="ADD")

    results = ceo_db.search_decisions_by_vector(
        decision.embedding,
        project_id=decision.project_id or None,
        limit=3,
    )

    if not results:
        return DedupResult(action="ADD")

    # Check for high similarity
    best = results[0]
    if not best.embedding:
        return DedupResult(action="ADD")

    dist = ceo_db._cosine_dist(decision.embedding, best.embedding)
    similarity = 1.0 - dist

    if similarity < 0.7:
        return DedupResult(action="ADD")

    # High similarity — same decision being updated?
    if similarity > 0.95:
        return DedupResult(
            action="UPDATE",
            existing_id=best.id,
            note=f"Very similar to existing decision (sim={similarity:.2f})",
        )

    # Medium similarity — use LLM if available
    if llm_complete and similarity > 0.7:
        try:
            prompt = (
                f"Are these the same decision being updated, or two different decisions?\n\n"
                f"Decision A: {best.title} — chose {best.choice}\n"
                f"Decision B: {decision.title} — chose {decision.choice}\n\n"
                f"Reply with JSON: {{\"action\": \"UPDATE\" or \"ADD\", \"reason\": \"...\"}}"
            )
            raw = llm_complete(prompt)
            import json
            parsed = json.loads(raw.strip())
            action = parsed.get("action", "ADD")
            if action == "UPDATE":
                return DedupResult(action="UPDATE", existing_id=best.id, note=parsed.get("reason", ""))
        except Exception:
            pass

    return DedupResult(action="ADD")


def dedup_principle(
    ceo_db: CeoDB,
    emb,
    principle: Principle,
    llm_complete: Callable[[str], str] | None = None,
) -> DedupResult:
    """Check for similar principles. If found, increment evidence or flag conflict."""
    if not principle.embedding:
        return DedupResult(action="ADD")

    results = ceo_db.search_principles_by_vector(
        principle.embedding,
        project_id=principle.project_id or None,
        limit=3,
    )

    if not results:
        return DedupResult(action="ADD")

    best = results[0]
    if not best.embedding:
        return DedupResult(action="ADD")

    dist = ceo_db._cosine_dist(principle.embedding, best.embedding)
    similarity = 1.0 - dist

    if similarity < 0.7:
        return DedupResult(action="ADD")

    # Very similar — same principle, increment evidence
    if similarity > 0.85:
        ceo_db.increment_evidence(best.id)
        return DedupResult(
            action="NOOP",
            existing_id=best.id,
            note=f"Same principle, incremented evidence (sim={similarity:.2f})",
        )

    # Medium similarity — check for contradiction via LLM
    if llm_complete:
        try:
            prompt = (
                f"Are these principles similar (supporting each other) or contradictory?\n\n"
                f"Principle A: {best.content}\n"
                f"Principle B: {principle.content}\n\n"
                f"Reply with JSON: {{\"action\": \"ADD\" or \"NOOP\" or \"CONFLICT\", \"reason\": \"...\"}}"
            )
            raw = llm_complete(prompt)
            import json
            parsed = json.loads(raw.strip())
            action = parsed.get("action", "ADD")
            if action == "NOOP":
                ceo_db.increment_evidence(best.id)
                return DedupResult(action="NOOP", existing_id=best.id, note=parsed.get("reason", ""))
            if action == "CONFLICT":
                return DedupResult(action="CONFLICT", existing_id=best.id, note=parsed.get("reason", ""))
        except Exception:
            pass

    return DedupResult(action="ADD")
