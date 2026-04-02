"""Project detection — map a conversation/cwd to a known project."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_core.ceo_db import CeoDB

logger = logging.getLogger(__name__)

# Directories that should never be auto-created as projects
_AUTO_CREATE_BLACKLIST = frozenset({
    "Downloads", "Desktop", "Documents", "tmp", "temp", "home",
    "Users", "var", "etc", "usr", "bin", "opt", "Library", "Applications",
    "Pictures", "Music", "Movies", "Public", "Volumes", "System",
})


def _is_valid_project_name(name: str) -> bool:
    """Reject directory names that are clearly not project names."""
    if not name or len(name) <= 1 or name in (".", "/"):
        return False
    if name in _AUTO_CREATE_BLACKLIST:
        return False
    # Hidden directories (e.g., .openclaw, .claude)
    if name.startswith("."):
        return False
    # User home directory basename (e.g., "tong", "auxten") — too generic
    home_basename = os.path.basename(os.path.expanduser("~"))
    if name == home_basename:
        return False
    return True


def _is_under_home_root(cwd: str) -> bool:
    """Check if cwd IS the home directory itself (not a subdir)."""
    home = os.path.expanduser("~")
    return os.path.normpath(cwd) == os.path.normpath(home)


def detect_project(
    ceo_db: CeoDB,
    cwd: str = "",
    content: str = "",
    session_meta: dict | None = None,
    emb=None,
    allow_auto_create: bool = False,
) -> str:
    """Return project_id for the given context. Empty string = unassigned.

    Priority:
    1. Explicit project_id in session_meta
    2. Compute cwd_candidate and content_candidate independently
    3. If content_candidate exists, prefer it (explicit mention > implicit CWD)
    4. Otherwise use cwd_candidate
    5. Semantic search via embedding (threshold > 0.7)
    6. Auto-create from cwd (only if allow_auto_create=True and not blacklisted)
    7. Return "" (unassigned)
    """
    # 1. Explicit override
    if session_meta and session_meta.get("project_id"):
        return session_meta["project_id"]

    # 2. Compute candidates independently
    cwd_candidate = ""
    content_candidate = ""

    if cwd:
        project = ceo_db.find_project_by_path(cwd)
        if project:
            cwd_candidate = project.id

    if content:
        content_lower = content.lower()
        projects = ceo_db.list_projects()
        for p in projects:
            if p.name and len(p.name) >= 2 and p.name.lower() in content_lower:
                content_candidate = p.id
                break

    # 3. Content mention wins over CWD (explicit > implicit)
    if content_candidate:
        if cwd_candidate and content_candidate != cwd_candidate:
            logger.info(
                "Project detection: content mention (%s) overrides CWD match (%s)",
                content_candidate[:8], cwd_candidate[:8],
            )
        return content_candidate

    # 4. Fall back to CWD match
    if cwd_candidate:
        return cwd_candidate

    # 5. Semantic search (tightened threshold: dist < 0.3 → similarity > 0.7)
    if content and emb is not None:
        try:
            query_vec = emb.encode_query(content[:500])
            results = ceo_db.search_projects_by_vector(query_vec, limit=1)
            if results:
                dist = ceo_db._cosine_dist(query_vec, results[0].embedding)
                if dist < 0.3:
                    return results[0].id
        except Exception:
            pass

    # 6. Auto-create from cwd (guarded) or adopt existing same-name project
    if allow_auto_create and cwd and not _is_under_home_root(cwd):
        project_name = os.path.basename(cwd)
        if not _is_valid_project_name(project_name):
            return ""

        # Check if a project with the same name already exists (different machine path)
        projects = ceo_db.list_projects()
        for p in projects:
            if p.name and p.name.lower() == project_name.lower():
                # Adopt: update repo_url to current CWD so future path-match works
                if p.repo_url != cwd:
                    ceo_db.update_project(p.id, repo_url=cwd)
                    logger.info(
                        "Adopted existing project '%s': updated repo_url %s → %s",
                        p.name, p.repo_url, cwd,
                    )
                return p.id

        from memory_core.models import Project
        p = Project(name=project_name, repo_url=cwd, status="building")
        if emb:
            p.embedding = emb.encode_document(project_name)
        pid = ceo_db.insert_project(p)
        logger.info("Auto-created project '%s' from cwd %s", project_name, cwd)
        return pid

    # 7. Unassigned
    return ""
