"""Tests for project detection module."""

from memory_core.project_detect import detect_project
from tests.helpers.factories import ProjectFactory


class TestDetectProject:

    def test_explicit_session_meta(self, ceo_db):
        result = detect_project(ceo_db, session_meta={"project_id": "explicit-id"})
        assert result == "explicit-id"

    def test_match_by_cwd(self, ceo_db, mock_emb):
        p = ProjectFactory.build(repo_url="/home/user/myproject")
        ceo_db.insert_project(p)
        result = detect_project(ceo_db, cwd="/home/user/myproject/src/main.py")
        assert result == p.id

    def test_match_by_name_in_content(self, ceo_db):
        p = ProjectFactory.build(name="ClickMem")
        ceo_db.insert_project(p)
        result = detect_project(ceo_db, content="Working on clickmem today")
        assert result == p.id

    def test_no_match_returns_empty(self, ceo_db, mock_emb):
        result = detect_project(ceo_db, cwd="/some/other/path", content="random stuff", emb=mock_emb)
        assert result == ""

    def test_priority_session_meta_over_cwd(self, ceo_db):
        p = ProjectFactory.build(repo_url="/home/user/myproject")
        ceo_db.insert_project(p)
        result = detect_project(
            ceo_db,
            cwd="/home/user/myproject/src",
            session_meta={"project_id": "override-id"},
        )
        assert result == "override-id"

    def test_empty_inputs(self, ceo_db):
        result = detect_project(ceo_db)
        assert result == ""
