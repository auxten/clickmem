"""Context Engine — assemble structured CEO context for injection at session start."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB


def build_ceo_context(
    ceo_db: CeoDB,
    emb,
    project_id: str = "",
    agent_source: str = "",
    task_hint: str = "",
    max_tokens: int = 1700,
) -> str:
    """Assemble CEO context for injection.

    Token budget (approx chars/4):
    - Project Info: ~200 tokens (800 chars)
    - Principles: ~300 tokens (1200 chars)
    - Recent Decisions: ~500 tokens (2000 chars)
    - Recent Episodes: ~200 tokens (800 chars)
    - Semantic Search: ~500 tokens (2000 chars) — only when task_hint provided
    """
    max_chars = max_tokens * 4
    sections: list[str] = []
    chars_remaining = max_chars

    # 1. Project Info
    if project_id:
        project = ceo_db.get_project(project_id)
        if project:
            section = _format_project(project)
            budget = min(800, chars_remaining)
            if len(section) > budget:
                section = section[:budget]
            sections.append(section)
            chars_remaining -= len(section)

    # 2. Principles (global + project-specific)
    principles_budget = min(1200, chars_remaining)
    if principles_budget > 50:
        principles = _fetch_principles(ceo_db, project_id, principles_budget)
        if principles:
            sections.append(principles)
            chars_remaining -= len(principles)

    # 3. Recent Decisions
    decisions_budget = min(2000, chars_remaining)
    if decisions_budget > 50:
        decisions = _fetch_decisions(ceo_db, project_id, decisions_budget)
        if decisions:
            sections.append(decisions)
            chars_remaining -= len(decisions)

    # 4. Recent Episodes
    episodes_budget = min(800, chars_remaining)
    if episodes_budget > 50:
        episodes = _fetch_episodes(ceo_db, project_id, episodes_budget)
        if episodes:
            sections.append(episodes)
            chars_remaining -= len(episodes)

    # 5. Semantic Search (only when task_hint provided)
    if task_hint and emb and chars_remaining > 100:
        search_budget = min(2000, chars_remaining)
        search_section = _fetch_semantic(ceo_db, emb, task_hint, project_id, search_budget)
        if search_section:
            sections.append(search_section)

    if not sections:
        return ""

    body = "\n\n".join(sections)
    return f"<clickmem-context>\n{body}\n</clickmem-context>"


def _format_project(project) -> str:
    lines = [f"## Project: {project.name}"]
    if project.description:
        lines.append(project.description)
    parts: list[str] = []
    if project.status:
        parts.append(f"Status: {project.status}")
    if project.vision:
        parts.append(f"Vision: {project.vision}")
    if project.target_users:
        parts.append(f"Users: {project.target_users}")
    if project.north_star_metric:
        parts.append(f"North Star: {project.north_star_metric}")
    if project.tech_stack:
        parts.append(f"Stack: {', '.join(project.tech_stack)}")
    if parts:
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _fetch_principles(ceo_db: CeoDB, project_id: str, budget: int) -> str:
    """Fetch global + project principles, sorted by confidence."""
    all_principles = []

    # Global principles
    global_p = ceo_db.list_principles(project_id="", active_only=True)
    all_principles.extend(global_p)

    # Project-specific principles
    if project_id:
        proj_p = ceo_db.list_principles(project_id=project_id, active_only=True)
        all_principles.extend(proj_p)

    if not all_principles:
        return ""

    # Sort by confidence desc, deduplicate by id
    seen = set()
    unique = []
    for p in sorted(all_principles, key=lambda x: x.confidence, reverse=True):
        if p.id not in seen:
            seen.add(p.id)
            unique.append(p)

    lines = ["## Principles"]
    chars = len(lines[0])
    for p in unique:
        line = f"- [{p.confidence:.0%}] {p.content}"
        if p.domain != "tech":
            line += f" ({p.domain})"
        if chars + len(line) + 1 > budget:
            break
        lines.append(line)
        chars += len(line) + 1

    return "\n".join(lines) if len(lines) > 1 else ""


def _fetch_decisions(ceo_db: CeoDB, project_id: str, budget: int) -> str:
    """Fetch recent decisions for this project."""
    decisions = ceo_db.list_decisions(
        project_id=project_id if project_id else None,
        limit=5,
    )
    if not decisions:
        return ""

    lines = ["## Recent Decisions"]
    chars = len(lines[0])
    for d in decisions:
        line = f"- {d.title}: {d.choice}"
        if d.reasoning:
            line += f" (because: {d.reasoning[:100]})"
        if chars + len(line) + 1 > budget:
            break
        lines.append(line)
        chars += len(line) + 1

    return "\n".join(lines) if len(lines) > 1 else ""


def _fetch_episodes(ceo_db: CeoDB, project_id: str, budget: int) -> str:
    """Fetch recent episode summaries."""
    episodes = ceo_db.list_episodes(
        project_id=project_id if project_id else None,
        limit=3,
    )
    if not episodes:
        return ""

    lines = ["## Recent Activity"]
    chars = len(lines[0])
    for e in episodes:
        line = f"- {e.content[:150]}"
        if e.user_intent:
            line += f" (intent: {e.user_intent[:80]})"
        if chars + len(line) + 1 > budget:
            break
        lines.append(line)
        chars += len(line) + 1

    return "\n".join(lines) if len(lines) > 1 else ""


def _fetch_semantic(
    ceo_db: CeoDB, emb, task_hint: str, project_id: str, budget: int,
) -> str:
    """Semantic search across all entity types using task_hint."""
    try:
        query_vec = emb.encode_query(task_hint[:500])
    except Exception:
        return ""

    results = ceo_db.search_all_by_vector(
        query_vec,
        project_id=project_id if project_id else None,
        limit=5,
    )
    if not results:
        return ""

    lines = ["## Relevant Context"]
    chars = len(lines[0])
    for r in results:
        label = r["entity_type"].capitalize()
        content = r["content"][:150]
        line = f"- [{label}] {content}"
        if chars + len(line) + 1 > budget:
            break
        lines.append(line)
        chars += len(line) + 1

    return "\n".join(lines) if len(lines) > 1 else ""
