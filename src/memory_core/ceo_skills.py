"""CEO Skills — 6 proactive capabilities for the CEO knowledge system.

Each skill is a standalone function returning a dict for the MCP/REST layer to format.
"""

from __future__ import annotations

import json
import logging
import math
from typing import TYPE_CHECKING, Callable, Optional

from memory_core.ceo_retrieval import ceo_search

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB

logger = logging.getLogger(__name__)


def _compute_scope_embedding(emb, scope_strings: list[str]) -> list[float] | None:
    """Compute centroid embedding from a list of scope strings."""
    if not scope_strings or not emb:
        return None
    vecs = [emb.encode_document(s) for s in scope_strings]
    if not vecs:
        return None
    dim = len(vecs[0])
    centroid = [0.0] * dim
    for v in vecs:
        for i in range(dim):
            centroid[i] += v[i]
    n = len(vecs)
    centroid = [x / n for x in centroid]
    # L2-normalize
    norm = math.sqrt(sum(x * x for x in centroid))
    if norm > 1e-10:
        centroid = [x / norm for x in centroid]
    return centroid


def ceo_brief(
    ceo_db: CeoDB, emb, project_id: str = "", query: str = "",
    session_id: str = "", task_context: str = "",
) -> dict:
    """Detailed project briefing. More detailed than context injection."""
    result: dict = {}

    if project_id:
        project = ceo_db.get_project(project_id)
        if project:
            result["project"] = {
                "name": project.name,
                "description": project.description,
                "status": project.status,
                "vision": project.vision,
                "target_users": project.target_users,
                "north_star_metric": project.north_star_metric,
                "tech_stack": project.tech_stack,
                "repo_url": project.repo_url,
            }

    # Fetch principles
    principles = ceo_db.list_principles(project_id=project_id if project_id else None)
    result["principles"] = [
        {"content": p.content, "confidence": p.confidence, "domain": p.domain,
         "evidence_count": p.evidence_count}
        for p in principles[:10]
    ]

    # Fetch recent decisions
    decisions = ceo_db.list_decisions(project_id=project_id if project_id else None, limit=10)
    result["decisions"] = [
        {"title": d.title, "choice": d.choice, "reasoning": d.reasoning,
         "outcome_status": d.outcome_status, "domain": d.domain}
        for d in decisions
    ]

    # Fetch recent episodes
    episodes = ceo_db.list_episodes(project_id=project_id if project_id else None, limit=5)
    result["recent_activity"] = [
        {"content": e.content[:200], "user_intent": e.user_intent}
        for e in episodes
    ]

    # Semantic search if query provided
    if query:
        search_results = ceo_search(
            ceo_db, emb, query, project_id=project_id, top_k=5,
            session_id=session_id or None, task_context=task_context or None,
        )
        result["query_results"] = search_results

    return result


def ceo_decide(
    ceo_db: CeoDB,
    emb,
    llm_complete: Callable[[str], str] | None,
    question: str,
    options: list[str] | None = None,
    context: str = "",
    project_id: str = "",
    session_id: str = "",
    task_context: str = "",
) -> dict:
    """Decision support with historical context."""
    # Find related decisions
    related = ceo_search(
        ceo_db, emb, question,
        project_id=project_id, entity_types=["decisions"], top_k=5,
        session_id=session_id or None, task_context=task_context or None,
    )

    # Find relevant principles
    principles = ceo_search(
        ceo_db, emb, question,
        project_id=project_id, entity_types=["principles"], top_k=5,
        session_id=session_id or None, task_context=task_context or None,
    )

    result = {
        "related_decisions": related,
        "relevant_principles": principles,
        "recommendation": None,
        "needs_ceo_confirmation": True,
    }

    if llm_complete:
        # Build prompt for LLM recommendation
        dec_text = "\n".join(
            f"- {r['content']} (status: {r['metadata'].get('outcome_status', 'unknown')})"
            for r in related[:3]
        )
        prin_text = "\n".join(
            f"- {r['content']} (confidence: {r['metadata'].get('confidence', 0):.0%})"
            for r in principles[:3]
        )
        options_text = "\n".join(f"- {o}" for o in (options or []))

        prompt = (
            f"Based on the CEO's past decisions and principles, recommend a decision.\n\n"
            f"Question: {question}\n"
            f"{f'Options:{chr(10)}{options_text}' if options_text else ''}\n"
            f"{f'Context: {context}' if context else ''}\n\n"
            f"Related past decisions:\n{dec_text or '(none)'}\n\n"
            f"Relevant principles:\n{prin_text or '(none)'}\n\n"
            f"Provide a recommendation as JSON: "
            f'{{"recommendation": "...", "confidence": 0.0-1.0, "reasoning": "..."}}'
        )

        try:
            raw = llm_complete(prompt)
            parsed = json.loads(raw.strip())
            result["recommendation"] = parsed.get("recommendation")
            result["confidence"] = parsed.get("confidence", 0.5)
            result["reasoning"] = parsed.get("reasoning", "")
        except Exception as e:
            logger.warning("ceo_decide LLM failed: %s", e)

    return result


def ceo_remember(
    ceo_db: CeoDB,
    emb,
    entity_type: str,
    content: dict,
    project_id: str = "",
    session_id: str = "",
) -> dict:
    """Structured memory storage for a specific entity type."""
    from memory_core.models import Decision, Episode, Principle

    # Update session topic if session_id provided
    if session_id and emb:
        from memory_core.session_context import get_session_store
        store = get_session_store()
        summary = content.get("title", "") or content.get("content", "")
        if summary:
            vec = emb.encode_query(summary[:500])
            store.update(session_id, vec, summary)

    if entity_type == "decision":
        activation_scope = content.get("activation_scope", [])
        scope_emb = _compute_scope_embedding(emb, activation_scope) if activation_scope else None
        d = Decision(
            project_id=project_id,
            title=content.get("title", ""),
            context=content.get("context", ""),
            choice=content.get("choice", ""),
            reasoning=content.get("reasoning", ""),
            alternatives=content.get("alternatives", ""),
            domain=content.get("domain", "tech"),
            activation_scope=activation_scope,
            scope_embedding=scope_emb,
        )
        embed_text = f"{d.title} {d.choice} {d.reasoning}"
        d.embedding = emb.encode_document(embed_text)
        did = ceo_db.insert_decision(d)
        return {"id": did, "type": "decision", "status": "stored"}

    elif entity_type == "principle":
        activation_scope = content.get("activation_scope", [])
        scope_emb = _compute_scope_embedding(emb, activation_scope) if activation_scope else None
        p = Principle(
            project_id=project_id,
            content=content.get("content", ""),
            domain=content.get("domain", "tech"),
            confidence=float(content.get("confidence", 0.7)),
            evidence_count=1,
            activation_scope=activation_scope,
            scope_embedding=scope_emb,
        )
        p.embedding = emb.encode_document(p.content)
        pid = ceo_db.insert_principle(p)
        return {"id": pid, "type": "principle", "status": "stored"}

    elif entity_type == "episode":
        e = Episode(
            project_id=project_id,
            content=content.get("content", ""),
            user_intent=content.get("user_intent", ""),
            key_outcomes=content.get("key_outcomes", []),
            domain=content.get("domain", "tech"),
        )
        e.embedding = emb.encode_document(e.content)
        eid = ceo_db.insert_episode(e)
        return {"id": eid, "type": "episode", "status": "stored"}

    return {"error": f"Unknown entity type: {entity_type}"}


def ceo_review(
    ceo_db: CeoDB,
    emb,
    llm_complete: Callable[[str], str] | None,
    proposed_plan: str,
    project_id: str = "",
    session_id: str = "",
    task_context: str = "",
) -> dict:
    """Consistency check against principles and past decisions."""
    # Find relevant principles and decisions
    principles = ceo_search(
        ceo_db, emb, proposed_plan,
        project_id=project_id, entity_types=["principles"], top_k=5,
        session_id=session_id or None, task_context=task_context or None,
    )
    decisions = ceo_search(
        ceo_db, emb, proposed_plan,
        project_id=project_id, entity_types=["decisions"], top_k=5,
        session_id=session_id or None, task_context=task_context or None,
    )

    result = {
        "relevant_principles": principles,
        "relevant_decisions": decisions,
        "consistent": None,
        "conflicts": [],
        "suggestions": [],
    }

    if llm_complete:
        prin_text = "\n".join(f"- {r['content']}" for r in principles[:5])
        dec_text = "\n".join(f"- {r['content']}" for r in decisions[:5])

        prompt = (
            f"Review this proposed plan for consistency with the CEO's principles and decisions.\n\n"
            f"Proposed plan:\n{proposed_plan}\n\n"
            f"CEO Principles:\n{prin_text or '(none)'}\n\n"
            f"Past Decisions:\n{dec_text or '(none)'}\n\n"
            f"Respond with JSON: {{\"consistent\": true/false, \"conflicts\": [\"...\"], "
            f"\"suggestions\": [\"...\"]}}"
        )

        try:
            raw = llm_complete(prompt)
            parsed = json.loads(raw.strip())
            result["consistent"] = parsed.get("consistent")
            result["conflicts"] = parsed.get("conflicts", [])
            result["suggestions"] = parsed.get("suggestions", [])
        except Exception as e:
            logger.warning("ceo_review LLM failed: %s", e)

    return result


def ceo_retro(
    ceo_db: CeoDB,
    emb,
    llm_complete: Callable[[str], str] | None,
    project_id: str,
    time_range: str = "",
    session_id: str = "",
) -> dict:
    """Retrospective: summarise decisions, identify patterns, suggest principles."""
    decisions = ceo_db.list_decisions(project_id=project_id, limit=20)
    episodes = ceo_db.list_episodes(project_id=project_id, limit=20)

    result = {
        "decision_summary": [
            {"title": d.title, "choice": d.choice, "outcome_status": d.outcome_status}
            for d in decisions
        ],
        "validated": [d.title for d in decisions if d.outcome_status == "validated"],
        "invalidated": [d.title for d in decisions if d.outcome_status == "invalidated"],
        "principle_candidates": [],
    }

    if llm_complete and (decisions or episodes):
        dec_text = "\n".join(
            f"- {d.title}: {d.choice} (outcome: {d.outcome_status})"
            for d in decisions[:10]
        )
        ep_text = "\n".join(f"- {e.content[:100]}" for e in episodes[:10])

        prompt = (
            f"Run a retrospective analysis for this project.\n\n"
            f"Decisions:\n{dec_text or '(none)'}\n\n"
            f"Recent episodes:\n{ep_text or '(none)'}\n\n"
            f"Respond with JSON: {{\"summary\": \"...\", \"validated\": [\"...\"], "
            f"\"invalidated\": [\"...\"], \"principle_candidates\": [\"...\"]}}"
        )

        try:
            raw = llm_complete(prompt)
            parsed = json.loads(raw.strip())
            result["principle_candidates"] = parsed.get("principle_candidates", [])
            if parsed.get("summary"):
                result["summary"] = parsed["summary"]

            # Auto-update decision outcomes based on retro analysis
            updated_ids = []
            for status_key in ("validated", "invalidated"):
                for title in parsed.get(status_key, []):
                    if not title:
                        continue
                    # Fuzzy match: find the decision with the closest title
                    title_lower = title.lower().strip()
                    for d in decisions:
                        if (
                            d.outcome_status == "pending"
                            and d.title.lower().strip() == title_lower
                        ):
                            ceo_db.update_decision(d.id, outcome_status=status_key)
                            updated_ids.append(d.id)
                            break
            if updated_ids:
                result["updated_decision_ids"] = updated_ids
                logger.info("ceo_retro updated %d decision outcomes", len(updated_ids))

        except Exception as e:
            logger.warning("ceo_retro LLM failed: %s", e)

    return result


def ceo_update_outcome(
    ceo_db: CeoDB,
    decision_id: str,
    outcome_status: str,
    outcome: str = "",
) -> dict:
    """Manually update a decision's outcome status.

    Args:
        decision_id: The decision to update.
        outcome_status: One of "validated", "invalidated", "unknown".
        outcome: Optional free-text description of the outcome.
    """
    valid = {"validated", "invalidated", "unknown", "pending"}
    if outcome_status not in valid:
        return {"error": f"Invalid status '{outcome_status}'. Must be one of {valid}"}

    d = ceo_db.get_decision(decision_id)
    if not d:
        return {"error": f"Decision '{decision_id}' not found"}

    fields = {"outcome_status": outcome_status}
    if outcome:
        fields["outcome"] = outcome
    ceo_db.update_decision(decision_id, **fields)

    return {
        "id": decision_id,
        "title": d.title,
        "old_status": d.outcome_status,
        "new_status": outcome_status,
        "outcome": outcome,
    }


def ceo_portfolio(ceo_db: CeoDB, emb, session_id: str = "") -> dict:
    """Cross-project overview: all active projects with recent activity."""
    projects = ceo_db.list_projects()

    items = []
    for p in projects:
        episodes = ceo_db.list_episodes(project_id=p.id, limit=3)
        decisions = ceo_db.list_decisions(project_id=p.id, limit=3)
        items.append({
            "id": p.id,
            "name": p.name,
            "status": p.status,
            "description": p.description[:100],
            "recent_episodes": len(episodes),
            "recent_decisions": len(decisions),
            "latest_activity": episodes[0].content[:100] if episodes else "",
        })

    counts = ceo_db.count_all()
    return {
        "projects": items,
        "totals": counts,
    }
