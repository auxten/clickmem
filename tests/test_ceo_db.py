"""Tests for CeoDB — CEO Brain multi-table storage."""

from __future__ import annotations

import pytest

from memory_core.ceo_db import CeoDB
from memory_core.models import Decision, Episode, Principle, Project
from tests.helpers.factories import (
    DecisionFactory,
    EpisodeFactory,
    PrincipleFactory,
    ProjectFactory,
)


# ======================================================================
# Projects
# ======================================================================

class TestProjectsCRUD:

    def test_insert_and_get(self, ceo_db):
        p = ProjectFactory.build(name="ClickMem", repo_url="/home/user/clickmem")
        ceo_db.insert_project(p)
        got = ceo_db.get_project(p.id)
        assert got is not None
        assert got.name == "ClickMem"
        assert got.repo_url == "/home/user/clickmem"

    def test_get_nonexistent(self, ceo_db):
        assert ceo_db.get_project("nonexistent") is None

    def test_update_project(self, ceo_db):
        p = ProjectFactory.build(name="Old Name")
        ceo_db.insert_project(p)
        ceo_db.update_project(p.id, name="New Name", status="launched")
        got = ceo_db.get_project(p.id)
        assert got.name == "New Name"
        assert got.status == "launched"

    def test_list_projects(self, ceo_db):
        p1 = ProjectFactory.build(status="building")
        p2 = ProjectFactory.build(status="launched")
        ceo_db.insert_project(p1)
        ceo_db.insert_project(p2)
        all_projects = ceo_db.list_projects()
        assert len(all_projects) == 2
        building = ceo_db.list_projects(status="building")
        assert len(building) == 1
        assert building[0].id == p1.id

    def test_find_project_by_path(self, ceo_db):
        p = ProjectFactory.build(repo_url="/home/user/myproject")
        ceo_db.insert_project(p)
        found = ceo_db.find_project_by_path("/home/user/myproject/src/main.py")
        assert found is not None
        assert found.id == p.id
        not_found = ceo_db.find_project_by_path("/home/user/other")
        assert not_found is None

    def test_search_projects_by_vector(self, ceo_db, mock_emb):
        p = ProjectFactory.build(name="VectorProject")
        p.embedding = mock_emb.encode_document("VectorProject test")
        ceo_db.insert_project(p)
        query_vec = mock_emb.encode_query("VectorProject")
        results = ceo_db.search_projects_by_vector(query_vec, limit=5)
        assert len(results) >= 1
        assert results[0].id == p.id


# ======================================================================
# Decisions
# ======================================================================

class TestDecisionsCRUD:

    def test_insert_and_get(self, ceo_db):
        d = DecisionFactory.build(title="Use chDB", choice="chDB")
        ceo_db.insert_decision(d)
        got = ceo_db.get_decision(d.id)
        assert got is not None
        assert got.title == "Use chDB"
        assert got.choice == "chDB"

    def test_update_decision(self, ceo_db):
        d = DecisionFactory.build(outcome_status="pending")
        ceo_db.insert_decision(d)
        ceo_db.update_decision(d.id, outcome_status="validated", outcome="Works great")
        got = ceo_db.get_decision(d.id)
        assert got.outcome_status == "validated"
        assert got.outcome == "Works great"

    def test_list_decisions_with_filters(self, ceo_db):
        d1 = DecisionFactory.build(project_id="proj1", domain="tech")
        d2 = DecisionFactory.build(project_id="proj1", domain="product")
        d3 = DecisionFactory.build(project_id="proj2", domain="tech")
        for d in [d1, d2, d3]:
            ceo_db.insert_decision(d)
        proj1 = ceo_db.list_decisions(project_id="proj1")
        assert len(proj1) == 2
        tech = ceo_db.list_decisions(domain="tech")
        assert len(tech) == 2

    def test_search_decisions_by_vector(self, ceo_db, mock_emb):
        d = DecisionFactory.build(title="Vector decision")
        d.embedding = mock_emb.encode_document("Vector decision")
        ceo_db.insert_decision(d)
        query_vec = mock_emb.encode_query("vector")
        results = ceo_db.search_decisions_by_vector(query_vec, limit=5)
        assert len(results) >= 1


# ======================================================================
# Principles
# ======================================================================

class TestPrinciplesCRUD:

    def test_insert_and_get(self, ceo_db):
        p = PrincipleFactory.build(content="Keep it simple")
        ceo_db.insert_principle(p)
        got = ceo_db.get_principle(p.id)
        assert got is not None
        assert got.content == "Keep it simple"

    def test_update_principle(self, ceo_db):
        p = PrincipleFactory.build(confidence=0.5)
        ceo_db.insert_principle(p)
        ceo_db.update_principle(p.id, confidence=0.9)
        got = ceo_db.get_principle(p.id)
        assert got.confidence == pytest.approx(0.9, abs=0.01)

    def test_increment_evidence(self, ceo_db):
        p = PrincipleFactory.build(confidence=0.5, evidence_count=1)
        ceo_db.insert_principle(p)
        ceo_db.increment_evidence(p.id)
        got = ceo_db.get_principle(p.id)
        assert got.evidence_count == 2
        assert got.confidence > 0.5

    def test_list_principles_active_only(self, ceo_db):
        p1 = PrincipleFactory.build(is_active=True)
        p2 = PrincipleFactory.build(is_active=False)
        ceo_db.insert_principle(p1)
        ceo_db.insert_principle(p2)
        active = ceo_db.list_principles(active_only=True)
        assert len(active) == 1
        all_p = ceo_db.list_principles(active_only=False)
        assert len(all_p) == 2

    def test_list_principles_sorted_by_confidence(self, ceo_db):
        p1 = PrincipleFactory.build(confidence=0.3)
        p2 = PrincipleFactory.build(confidence=0.9)
        p3 = PrincipleFactory.build(confidence=0.6)
        for p in [p1, p2, p3]:
            ceo_db.insert_principle(p)
        result = ceo_db.list_principles()
        confidences = [p.confidence for p in result]
        assert confidences == sorted(confidences, reverse=True)

    def test_search_principles_by_vector(self, ceo_db, mock_emb):
        p = PrincipleFactory.build(content="Local first always")
        p.embedding = mock_emb.encode_document("Local first always")
        ceo_db.insert_principle(p)
        query_vec = mock_emb.encode_query("local")
        results = ceo_db.search_principles_by_vector(query_vec, limit=5)
        assert len(results) >= 1


# ======================================================================
# Episodes
# ======================================================================

class TestEpisodesCRUD:

    def test_insert_and_list(self, ceo_db):
        e = EpisodeFactory.build(content="Did some coding")
        ceo_db.insert_episode(e)
        episodes = ceo_db.list_episodes()
        assert len(episodes) == 1
        assert episodes[0].content == "Did some coding"

    def test_list_episodes_by_project(self, ceo_db):
        e1 = EpisodeFactory.build(project_id="proj1")
        e2 = EpisodeFactory.build(project_id="proj2")
        ceo_db.insert_episode(e1)
        ceo_db.insert_episode(e2)
        proj1 = ceo_db.list_episodes(project_id="proj1")
        assert len(proj1) == 1

    def test_search_episodes_by_vector(self, ceo_db, mock_emb):
        e = EpisodeFactory.build(content="Debugging vector search")
        e.embedding = mock_emb.encode_document("Debugging vector search")
        ceo_db.insert_episode(e)
        query_vec = mock_emb.encode_query("vector search")
        results = ceo_db.search_episodes_by_vector(query_vec, limit=5)
        assert len(results) >= 1


# ======================================================================
# Cross-entity search
# ======================================================================

class TestCrossEntitySearch:

    def test_search_all_by_vector(self, ceo_db, mock_emb):
        d = DecisionFactory.build(title="Use chDB")
        d.embedding = mock_emb.encode_document("Use chDB for storage")
        ceo_db.insert_decision(d)

        p = PrincipleFactory.build(content="Local-first architecture")
        p.embedding = mock_emb.encode_document("Local-first architecture")
        ceo_db.insert_principle(p)

        e = EpisodeFactory.build(content="Set up database layer")
        e.embedding = mock_emb.encode_document("Set up database layer")
        ceo_db.insert_episode(e)

        query_vec = mock_emb.encode_query("database")
        results = ceo_db.search_all_by_vector(query_vec, limit=10)
        assert len(results) == 3
        entity_types = {r["entity_type"] for r in results}
        assert entity_types == {"decision", "principle", "episode"}

    def test_search_all_empty_db(self, ceo_db, mock_emb):
        query_vec = mock_emb.encode_query("anything")
        results = ceo_db.search_all_by_vector(query_vec)
        assert results == []


# ======================================================================
# Stats and truncate
# ======================================================================

class TestStats:

    def test_count_all(self, ceo_db):
        ceo_db.insert_project(ProjectFactory.build())
        ceo_db.insert_decision(DecisionFactory.build())
        ceo_db.insert_decision(DecisionFactory.build())
        ceo_db.insert_principle(PrincipleFactory.build())
        ceo_db.insert_episode(EpisodeFactory.build())
        counts = ceo_db.count_all()
        assert counts["projects"] == 1
        assert counts["decisions"] == 2
        assert counts["principles"] == 1
        assert counts["episodes"] == 1

    def test_truncate(self, ceo_db):
        ceo_db.insert_project(ProjectFactory.build())
        ceo_db.insert_decision(DecisionFactory.build())
        ceo_db._truncate()
        counts = ceo_db.count_all()
        assert all(v == 0 for v in counts.values())


# ======================================================================
# Populated fixture smoke test
# ======================================================================

class TestPopulatedCeoDB:

    def test_populated_has_data(self, populated_ceo_db):
        counts = populated_ceo_db.count_all()
        assert counts["projects"] == 2
        assert counts["decisions"] == 3
        assert counts["principles"] == 3
        assert counts["episodes"] == 5
