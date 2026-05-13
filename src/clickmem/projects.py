"""Project detection + link management.

Project id is detected at ingest time from cwd → git remote → repo URL → id,
then **frozen** on every memory. There is no automatic cross-project recall:
to surface memories from another project the caller must either pass
``cross_project=True`` to recall, or use ``link()`` to whitelist the pair.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import List, Optional

from clickmem.backend import Backend, get_backend
from clickmem.embedding import embed
from clickmem.models import Project
from clickmem.sqlutil import quote_array_float, quote_array_str, quote_str, utc_now_sql


_log = logging.getLogger(__name__)


def _git_remote(cwd: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _normalise_repo_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    m = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
    if m:
        host, path = m.group(1), m.group(2)
        return f"https://{host}/{path}"
    if url.endswith(".git"):
        url = url[:-4]
    return url


def project_id_for(repo_url: str, name: str = "") -> str:
    key = repo_url or name or ""
    if not key:
        return ""
    return hashlib.sha1(key.lower().encode("utf-8")).hexdigest()[:16]


def detect_from_cwd(cwd: Optional[Path] = None) -> Project:
    cwd = Path(cwd or os.getcwd()).expanduser().resolve()
    repo_url = _normalise_repo_url(_git_remote(cwd))
    if not repo_url:
        try:
            top = subprocess.check_output(
                ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace").strip()
            repo_url = f"file://{top}"
            name = Path(top).name
        except Exception:
            repo_url = f"file://{cwd}"
            name = cwd.name
    else:
        name = repo_url.rsplit("/", 1)[-1]

    pid = project_id_for(repo_url, name)
    return Project(id=pid, name=name, repo_url=repo_url, kind="work")


def get(project_id: str, backend: Backend | None = None) -> Project | None:
    if not project_id:
        return None
    backend = backend or get_backend()
    rows = backend.query(
        "SELECT id, name, repo_url, kind, allowed_cross_refs, "
        "toString(created_at) AS created_at, toString(updated_at) AS updated_at "
        f"FROM projects FINAL WHERE id = {quote_str(project_id)} LIMIT 1"
    )
    return Project.from_row(rows[0]) if rows else None


def upsert(project: Project, backend: Backend | None = None) -> Project:
    backend = backend or get_backend()
    if not project.id:
        project.id = project_id_for(project.repo_url, project.name) or uuid.uuid4().hex[:16]
    if not project.embedding:
        try:
            project.embedding = embed(f"{project.name} {project.repo_url}")
        except Exception as e:
            _log.debug("project embed failed: %s", e)
            project.embedding = []
    sql = (
        "INSERT INTO projects "
        "(id, name, repo_url, kind, allowed_cross_refs, embedding, created_at, updated_at) VALUES ("
        f"{quote_str(project.id)}, {quote_str(project.name)}, {quote_str(project.repo_url)}, "
        f"{quote_str(project.kind)}, {quote_array_str(project.allowed_cross_refs)}, "
        f"{quote_array_float(project.embedding)}, {utc_now_sql()}, {utc_now_sql()}"
        ")"
    )
    backend.execute(sql)
    return project


def list_all(backend: Backend | None = None) -> List[Project]:
    backend = backend or get_backend()
    rows = backend.query(
        "SELECT id, name, repo_url, kind, allowed_cross_refs, "
        "toString(created_at) AS created_at, toString(updated_at) AS updated_at "
        "FROM projects FINAL ORDER BY updated_at DESC"
    )
    return [Project.from_row(r) for r in rows]


def link(a: str, b: str, reason: str = "", backend: Backend | None = None) -> tuple[Project, Project]:
    backend = backend or get_backend()
    pa = get(a, backend=backend)
    pb = get(b, backend=backend)
    if pa is None:
        pa = Project(id=a, name=a)
    if pb is None:
        pb = Project(id=b, name=b)
    if b not in pa.allowed_cross_refs:
        pa.allowed_cross_refs.append(b)
    if a not in pb.allowed_cross_refs:
        pb.allowed_cross_refs.append(a)
    upsert(pa, backend=backend)
    upsert(pb, backend=backend)
    return pa, pb


def allowed_cross_refs(a: str, b: str, backend: Backend | None = None) -> bool:
    if not a or not b or a == b:
        return False
    pa = get(a, backend=backend)
    if pa and b in (pa.allowed_cross_refs or []):
        return True
    pb = get(b, backend=backend)
    if pb and a in (pb.allowed_cross_refs or []):
        return True
    return False
