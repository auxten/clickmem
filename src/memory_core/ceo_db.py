"""CeoDB — CEO Brain persistent storage backed by chDB (embedded ClickHouse).

New multi-table schema for projects, decisions, principles, and episodes.
Shares the chDB session singleton with MemoryDB for coexistence during migration.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from memory_core.db import _get_session, _parse_dt
from memory_core.models import Decision, Episode, Principle, Project

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_PROJECTS = """
CREATE TABLE IF NOT EXISTS projects (
    id              String,
    name            String,
    description     String DEFAULT '',
    status          String DEFAULT 'building',
    vision          String DEFAULT '',
    target_users    String DEFAULT '',
    north_star_metric String DEFAULT '',
    tech_stack      Array(String),
    repo_url        String DEFAULT '',
    related_files   Array(String),
    metadata        String DEFAULT '',
    embedding       Array(Float32),
    created_at      DateTime64(3, 'UTC'),
    updated_at      DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (id)
"""

_CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS decisions (
    id              String,
    project_id      String DEFAULT '',
    title           String,
    context         String DEFAULT '',
    choice          String DEFAULT '',
    reasoning       String DEFAULT '',
    alternatives    String DEFAULT '',
    outcome         String DEFAULT '',
    outcome_status  String DEFAULT 'pending',
    domain          String DEFAULT 'tech',
    tags            Array(String),
    source_episodes Array(String),
    embedding       Array(Float32),
    created_at      DateTime64(3, 'UTC'),
    updated_at      DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (id)
"""

_CREATE_PRINCIPLES = """
CREATE TABLE IF NOT EXISTS principles (
    id              String,
    project_id      String DEFAULT '',
    content         String,
    domain          String DEFAULT 'tech',
    confidence      Float32 DEFAULT 0.5,
    evidence_count  UInt32 DEFAULT 0,
    source_decisions Array(String),
    embedding       Array(Float32),
    is_active       UInt8 DEFAULT 1,
    created_at      DateTime64(3, 'UTC'),
    updated_at      DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (id)
"""

_CREATE_EPISODES = """
CREATE TABLE IF NOT EXISTS episodes (
    id              String,
    project_id      String DEFAULT '',
    session_id      String DEFAULT '',
    agent_source    String DEFAULT '',
    content         String,
    user_intent     String DEFAULT '',
    key_outcomes    Array(String),
    domain          String DEFAULT 'tech',
    tags            Array(String),
    entities        Array(String),
    raw_id          String DEFAULT '',
    embedding       Array(Float32),
    created_at      DateTime64(3, 'UTC')
) ENGINE = MergeTree()
ORDER BY (created_at, id)
TTL toDateTime(created_at) + INTERVAL 180 DAY
"""


class CeoDB:
    """CEO Brain storage with four entity tables plus raw_transcripts."""

    def __init__(self, db_path: str = ":memory:"):
        self._session = _get_session(db_path)
        self._session.query(_CREATE_PROJECTS)
        self._session.query(_CREATE_DECISIONS)
        self._session.query(_CREATE_PRINCIPLES)
        self._session.query(_CREATE_EPISODES)

    # -- internal helpers --------------------------------------------------

    def _now_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def _escape(self, s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'")

    def _array_literal(self, items: list[str]) -> str:
        escaped = [f"'{self._escape(t)}'" for t in items]
        return f"[{','.join(escaped)}]"

    def _float_array_literal(self, items: list[float]) -> str:
        return f"[{','.join(str(x) for x in items)}]"

    def _query_json(self, sql: str) -> list[dict]:
        result = self._session.query(sql, "JSON")
        if result is None:
            return []
        raw = result.bytes()
        if not raw:
            return []
        parsed = json.loads(raw)
        return parsed.get("data", [])

    def _truncate(self) -> None:
        """Drop all data from CEO tables (for test isolation)."""
        for tbl in ("projects", "decisions", "principles", "episodes"):
            self._session.query(f"TRUNCATE TABLE IF EXISTS {tbl}")

    def query(self, sql: str) -> list[dict]:
        return self._query_json(sql)

    # ======================================================================
    # Projects CRUD
    # ======================================================================

    def insert_project(self, project: Project) -> str:
        now = self._now_str()
        created = project.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if project.created_at else now
        updated = project.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if project.updated_at else now
        emb = self._float_array_literal(project.embedding) if project.embedding else "[]"

        sql = (
            f"INSERT INTO projects (id, name, description, status, vision, "
            f"target_users, north_star_metric, tech_stack, repo_url, related_files, "
            f"metadata, embedding, created_at, updated_at) VALUES "
            f"('{self._escape(project.id)}', '{self._escape(project.name)}', "
            f"'{self._escape(project.description)}', '{self._escape(project.status)}', "
            f"'{self._escape(project.vision)}', '{self._escape(project.target_users)}', "
            f"'{self._escape(project.north_star_metric)}', "
            f"{self._array_literal(project.tech_stack)}, "
            f"'{self._escape(project.repo_url)}', "
            f"{self._array_literal(project.related_files)}, "
            f"'{self._escape(project.metadata)}', {emb}, '{created}', '{updated}')"
        )
        self._session.query(sql)
        return project.id

    def get_project(self, project_id: str) -> Project | None:
        rows = self._query_json(
            f"SELECT * FROM projects FINAL "
            f"WHERE id = '{self._escape(project_id)}' LIMIT 1"
        )
        if not rows:
            return None
        return self._row_to_project(rows[0])

    def update_project(self, project_id: str, **fields) -> None:
        """Update project fields via ReplacingMergeTree insert."""
        existing = self.get_project(project_id)
        if existing is None:
            return
        for k, v in fields.items():
            if hasattr(existing, k):
                setattr(existing, k, v)
        existing.updated_at = datetime.now(timezone.utc)
        self.insert_project(existing)

    def list_projects(self, status: str | None = None) -> list[Project]:
        conds = ["1 = 1"]
        if status:
            conds.append(f"status = '{self._escape(status)}'")
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT * FROM projects FINAL WHERE {where} ORDER BY updated_at DESC"
        )
        return [self._row_to_project(r) for r in rows]

    def find_project_by_path(self, cwd: str) -> Project | None:
        """Find a project whose repo_url is a prefix of cwd."""
        if not cwd:
            return None
        rows = self._query_json(
            "SELECT * FROM projects FINAL WHERE repo_url != '' ORDER BY length(repo_url) DESC"
        )
        for r in rows:
            repo = r.get("repo_url", "")
            if repo and cwd.startswith(repo):
                return self._row_to_project(r)
        return None

    def search_projects_by_vector(self, query_vec: list[float], limit: int = 5) -> list[Project]:
        vec_literal = self._float_array_literal(query_vec)
        rows = self._query_json(
            f"SELECT *, cosineDistance(embedding, {vec_literal}) AS _dist "
            f"FROM projects FINAL WHERE length(embedding) > 0 "
            f"ORDER BY _dist ASC LIMIT {limit}"
        )
        return [self._row_to_project(r) for r in rows]

    def _row_to_project(self, row: dict) -> Project:
        return Project(
            id=row["id"],
            name=row.get("name", ""),
            description=row.get("description", ""),
            status=row.get("status", "building"),
            vision=row.get("vision", ""),
            target_users=row.get("target_users", ""),
            north_star_metric=row.get("north_star_metric", ""),
            tech_stack=row.get("tech_stack", []),
            repo_url=row.get("repo_url", ""),
            related_files=row.get("related_files", []),
            metadata=row.get("metadata", ""),
            embedding=row.get("embedding"),
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )

    # ======================================================================
    # Decisions CRUD
    # ======================================================================

    def insert_decision(self, decision: Decision) -> str:
        now = self._now_str()
        created = decision.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if decision.created_at else now
        updated = decision.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if decision.updated_at else now
        emb = self._float_array_literal(decision.embedding) if decision.embedding else "[]"

        sql = (
            f"INSERT INTO decisions (id, project_id, title, context, choice, "
            f"reasoning, alternatives, outcome, outcome_status, domain, "
            f"tags, source_episodes, embedding, created_at, updated_at) VALUES "
            f"('{self._escape(decision.id)}', '{self._escape(decision.project_id)}', "
            f"'{self._escape(decision.title)}', '{self._escape(decision.context)}', "
            f"'{self._escape(decision.choice)}', '{self._escape(decision.reasoning)}', "
            f"'{self._escape(decision.alternatives)}', '{self._escape(decision.outcome)}', "
            f"'{self._escape(decision.outcome_status)}', '{self._escape(decision.domain)}', "
            f"{self._array_literal(decision.tags)}, "
            f"{self._array_literal(decision.source_episodes)}, "
            f"{emb}, '{created}', '{updated}')"
        )
        self._session.query(sql)
        return decision.id

    def get_decision(self, decision_id: str) -> Decision | None:
        rows = self._query_json(
            f"SELECT * FROM decisions FINAL "
            f"WHERE id = '{self._escape(decision_id)}' LIMIT 1"
        )
        if not rows:
            return None
        return self._row_to_decision(rows[0])

    def update_decision(self, decision_id: str, **fields) -> None:
        existing = self.get_decision(decision_id)
        if existing is None:
            return
        for k, v in fields.items():
            if hasattr(existing, k):
                setattr(existing, k, v)
        existing.updated_at = datetime.now(timezone.utc)
        self.insert_decision(existing)

    def list_decisions(
        self,
        project_id: str | None = None,
        domain: str | None = None,
        limit: int = 20,
    ) -> list[Decision]:
        conds = ["1 = 1"]
        if project_id is not None:
            conds.append(f"project_id = '{self._escape(project_id)}'")
        if domain:
            conds.append(f"domain = '{self._escape(domain)}'")
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT * FROM decisions FINAL WHERE {where} "
            f"ORDER BY created_at DESC LIMIT {limit}"
        )
        return [self._row_to_decision(r) for r in rows]

    def search_decisions_by_vector(
        self,
        query_vec: list[float],
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[Decision]:
        vec_literal = self._float_array_literal(query_vec)
        conds = ["length(embedding) > 0"]
        if project_id is not None:
            conds.append(f"project_id = '{self._escape(project_id)}'")
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT *, cosineDistance(embedding, {vec_literal}) AS _dist "
            f"FROM decisions FINAL WHERE {where} "
            f"ORDER BY _dist ASC LIMIT {limit}"
        )
        return [self._row_to_decision(r) for r in rows]

    def _row_to_decision(self, row: dict) -> Decision:
        return Decision(
            id=row["id"],
            project_id=row.get("project_id", ""),
            title=row.get("title", ""),
            context=row.get("context", ""),
            choice=row.get("choice", ""),
            reasoning=row.get("reasoning", ""),
            alternatives=row.get("alternatives", ""),
            outcome=row.get("outcome", ""),
            outcome_status=row.get("outcome_status", "pending"),
            domain=row.get("domain", "tech"),
            tags=row.get("tags", []),
            source_episodes=row.get("source_episodes", []),
            embedding=row.get("embedding"),
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )

    # ======================================================================
    # Principles CRUD
    # ======================================================================

    def insert_principle(self, principle: Principle) -> str:
        now = self._now_str()
        created = principle.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if principle.created_at else now
        updated = principle.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if principle.updated_at else now
        emb = self._float_array_literal(principle.embedding) if principle.embedding else "[]"
        is_active = 1 if principle.is_active else 0

        sql = (
            f"INSERT INTO principles (id, project_id, content, domain, confidence, "
            f"evidence_count, source_decisions, embedding, is_active, "
            f"created_at, updated_at) VALUES "
            f"('{self._escape(principle.id)}', '{self._escape(principle.project_id)}', "
            f"'{self._escape(principle.content)}', '{self._escape(principle.domain)}', "
            f"{principle.confidence}, {principle.evidence_count}, "
            f"{self._array_literal(principle.source_decisions)}, "
            f"{emb}, {is_active}, '{created}', '{updated}')"
        )
        self._session.query(sql)
        return principle.id

    def get_principle(self, principle_id: str) -> Principle | None:
        rows = self._query_json(
            f"SELECT * FROM principles FINAL "
            f"WHERE id = '{self._escape(principle_id)}' LIMIT 1"
        )
        if not rows:
            return None
        return self._row_to_principle(rows[0])

    def update_principle(self, principle_id: str, **fields) -> None:
        existing = self.get_principle(principle_id)
        if existing is None:
            return
        for k, v in fields.items():
            if hasattr(existing, k):
                setattr(existing, k, v)
        existing.updated_at = datetime.now(timezone.utc)
        self.insert_principle(existing)

    def list_principles(
        self,
        project_id: str | None = None,
        domain: str | None = None,
        active_only: bool = True,
    ) -> list[Principle]:
        conds: list[str] = []
        if active_only:
            conds.append("is_active = 1")
        if project_id is not None:
            conds.append(f"project_id = '{self._escape(project_id)}'")
        if domain:
            conds.append(f"domain = '{self._escape(domain)}'")
        where = " AND ".join(conds) if conds else "1 = 1"
        rows = self._query_json(
            f"SELECT * FROM principles FINAL WHERE {where} "
            f"ORDER BY confidence DESC, evidence_count DESC"
        )
        return [self._row_to_principle(r) for r in rows]

    def increment_evidence(self, principle_id: str) -> None:
        """Bump evidence_count and slightly increase confidence."""
        existing = self.get_principle(principle_id)
        if existing is None:
            return
        existing.evidence_count += 1
        # Asymptotically approach 1.0: new = old + (1 - old) * 0.1
        existing.confidence = min(1.0, existing.confidence + (1.0 - existing.confidence) * 0.1)
        existing.updated_at = datetime.now(timezone.utc)
        self.insert_principle(existing)

    def search_principles_by_vector(
        self,
        query_vec: list[float],
        project_id: str | None = None,
        limit: int = 10,
    ) -> list[Principle]:
        vec_literal = self._float_array_literal(query_vec)
        conds = ["length(embedding) > 0", "is_active = 1"]
        if project_id is not None:
            conds.append(f"project_id = '{self._escape(project_id)}'")
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT *, cosineDistance(embedding, {vec_literal}) AS _dist "
            f"FROM principles FINAL WHERE {where} "
            f"ORDER BY _dist ASC LIMIT {limit}"
        )
        return [self._row_to_principle(r) for r in rows]

    def _row_to_principle(self, row: dict) -> Principle:
        return Principle(
            id=row["id"],
            project_id=row.get("project_id", ""),
            content=row.get("content", ""),
            domain=row.get("domain", "tech"),
            confidence=float(row.get("confidence", 0.5)),
            evidence_count=int(row.get("evidence_count", 0)),
            source_decisions=row.get("source_decisions", []),
            embedding=row.get("embedding"),
            is_active=bool(int(row.get("is_active", 1))),
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )

    # ======================================================================
    # Episodes CRUD
    # ======================================================================

    def insert_episode(self, episode: Episode) -> str:
        now = self._now_str()
        created = episode.created_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if episode.created_at else now
        emb = self._float_array_literal(episode.embedding) if episode.embedding else "[]"

        sql = (
            f"INSERT INTO episodes (id, project_id, session_id, agent_source, "
            f"content, user_intent, key_outcomes, domain, tags, entities, "
            f"raw_id, embedding, created_at) VALUES "
            f"('{self._escape(episode.id)}', '{self._escape(episode.project_id)}', "
            f"'{self._escape(episode.session_id)}', '{self._escape(episode.agent_source)}', "
            f"'{self._escape(episode.content)}', '{self._escape(episode.user_intent)}', "
            f"{self._array_literal(episode.key_outcomes)}, "
            f"'{self._escape(episode.domain)}', "
            f"{self._array_literal(episode.tags)}, "
            f"{self._array_literal(episode.entities)}, "
            f"'{self._escape(episode.raw_id)}', {emb}, '{created}')"
        )
        self._session.query(sql)
        return episode.id

    def list_episodes(
        self,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[Episode]:
        conds = ["1 = 1"]
        if project_id is not None:
            conds.append(f"project_id = '{self._escape(project_id)}'")
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT * FROM episodes WHERE {where} "
            f"ORDER BY created_at DESC LIMIT {limit}"
        )
        return [self._row_to_episode(r) for r in rows]

    def search_episodes_by_vector(
        self,
        query_vec: list[float],
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[Episode]:
        vec_literal = self._float_array_literal(query_vec)
        conds = ["length(embedding) > 0"]
        if project_id is not None:
            conds.append(f"project_id = '{self._escape(project_id)}'")
        where = " AND ".join(conds)
        rows = self._query_json(
            f"SELECT *, cosineDistance(embedding, {vec_literal}) AS _dist "
            f"FROM episodes WHERE {where} "
            f"ORDER BY _dist ASC LIMIT {limit}"
        )
        return [self._row_to_episode(r) for r in rows]

    def _row_to_episode(self, row: dict) -> Episode:
        return Episode(
            id=row["id"],
            project_id=row.get("project_id", ""),
            session_id=row.get("session_id", ""),
            agent_source=row.get("agent_source", ""),
            content=row.get("content", ""),
            user_intent=row.get("user_intent", ""),
            key_outcomes=row.get("key_outcomes", []),
            domain=row.get("domain", "tech"),
            tags=row.get("tags", []),
            entities=row.get("entities", []),
            raw_id=row.get("raw_id", ""),
            embedding=row.get("embedding"),
            created_at=_parse_dt(row.get("created_at")),
        )

    # ======================================================================
    # Cross-entity search
    # ======================================================================

    def search_all_by_vector(
        self,
        query_vec: list[float],
        project_id: str | None = None,
        limit: int = 15,
    ) -> list[dict]:
        """Search across decisions, principles, and episodes. Returns unified dicts."""
        results: list[dict] = []

        # Search each entity type
        decisions = self.search_decisions_by_vector(query_vec, project_id=project_id, limit=limit)
        for d in decisions:
            dist = self._cosine_dist(query_vec, d.embedding) if d.embedding else 1.0
            results.append({
                "entity_type": "decision",
                "id": d.id,
                "content": f"{d.title}: {d.choice}",
                "score": 1.0 - dist,
                "metadata": {"reasoning": d.reasoning, "domain": d.domain,
                              "outcome_status": d.outcome_status},
            })

        principles = self.search_principles_by_vector(query_vec, project_id=project_id, limit=limit)
        for p in principles:
            dist = self._cosine_dist(query_vec, p.embedding) if p.embedding else 1.0
            results.append({
                "entity_type": "principle",
                "id": p.id,
                "content": p.content,
                "score": (1.0 - dist) * (0.5 + 0.5 * p.confidence),
                "metadata": {"confidence": p.confidence, "evidence_count": p.evidence_count,
                              "domain": p.domain},
            })

        episodes = self.search_episodes_by_vector(query_vec, project_id=project_id, limit=limit)
        for e in episodes:
            dist = self._cosine_dist(query_vec, e.embedding) if e.embedding else 1.0
            results.append({
                "entity_type": "episode",
                "id": e.id,
                "content": e.content[:200],
                "score": 1.0 - dist,
                "metadata": {"user_intent": e.user_intent, "domain": e.domain},
            })

        # Sort by score desc and limit
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    @staticmethod
    def _cosine_dist(a: list[float], b: list[float] | None) -> float:
        """Compute cosine distance between two vectors."""
        if not b or len(a) != len(b):
            return 1.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 1.0
        return 1.0 - dot / (norm_a * norm_b)

    # ======================================================================
    # Stats
    # ======================================================================

    def count_all(self) -> dict[str, int]:
        """Return counts for each CEO entity table."""
        result = {}
        for tbl in ("projects", "decisions", "principles", "episodes"):
            final = " FINAL" if tbl != "episodes" else ""
            rows = self._query_json(f"SELECT count() as cnt FROM {tbl}{final}")
            result[tbl] = int(rows[0]["cnt"]) if rows else 0
        return result
