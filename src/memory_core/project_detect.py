"""Project detection — map a conversation/cwd to a known project."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB


def detect_project(
    ceo_db: CeoDB,
    cwd: str = "",
    content: str = "",
    session_meta: dict | None = None,
    emb=None,
) -> str:
    """Return project_id for the given context. Empty string = unassigned.

    Priority:
    1. Explicit project_id in session_meta
    2. cwd path matches a project's repo_url (prefix match)
    3. Content mentions a known project name (case-insensitive)
    4. Semantic search via embedding (threshold > 0.6)
    5. Return "" (unassigned)
    """
    # 1. Explicit override
    if session_meta and session_meta.get("project_id"):
        return session_meta["project_id"]

    # 2. Path match
    if cwd:
        project = ceo_db.find_project_by_path(cwd)
        if project:
            return project.id

    # 3. Name mention in content
    if content:
        content_lower = content.lower()
        projects = ceo_db.list_projects()
        for p in projects:
            if p.name and p.name.lower() in content_lower:
                return p.id

    # 4. Semantic search
    if content and emb is not None:
        try:
            query_vec = emb.encode_query(content[:500])
            results = ceo_db.search_projects_by_vector(query_vec, limit=1)
            if results:
                # Check similarity threshold (cosine distance < 0.4 → similarity > 0.6)
                dist = ceo_db._cosine_dist(query_vec, results[0].embedding)
                if dist < 0.4:
                    return results[0].id
        except Exception:
            pass

    # 5. Unassigned
    return ""
