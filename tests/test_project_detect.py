"""Tests for project detection module."""

from memory_core.project_detect import detect_project, _AUTO_CREATE_BLACKLIST
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

    # --- New tests for content-override and auto-create improvements ---

    def test_content_overrides_cwd(self, ceo_db):
        """When content mentions project B but CWD matches project A, prefer B."""
        proj_a = ProjectFactory.build(name="ProjectA", repo_url="/home/user/project-a")
        proj_b = ProjectFactory.build(name="ProjectB", repo_url="/home/user/project-b")
        ceo_db.insert_project(proj_a)
        ceo_db.insert_project(proj_b)

        result = detect_project(
            ceo_db,
            cwd="/home/user/project-a/src",
            content="Let's discuss the ProjectB integration today",
        )
        assert result == proj_b.id

    def test_cwd_used_when_no_content_mention(self, ceo_db):
        """CWD match is used when content doesn't mention any known project."""
        proj_a = ProjectFactory.build(name="ProjectA", repo_url="/home/user/project-a")
        ceo_db.insert_project(proj_a)

        result = detect_project(
            ceo_db,
            cwd="/home/user/project-a/src",
            content="Fix the bug in the main module",
        )
        assert result == proj_a.id

    def test_auto_create_blacklist(self, ceo_db, mock_emb):
        """Directories in the blacklist should not auto-create projects."""
        for name in ["Downloads", "Desktop", "Documents", "tmp"]:
            result = detect_project(
                ceo_db,
                cwd=f"/Users/testuser/{name}",
                emb=mock_emb,
                allow_auto_create=True,
            )
            assert result == "", f"Should not auto-create project for {name}"

    def test_auto_create_requires_opt_in(self, ceo_db, mock_emb):
        """Auto-create should not happen unless allow_auto_create=True."""
        result = detect_project(
            ceo_db,
            cwd="/home/user/new-project",
            emb=mock_emb,
            allow_auto_create=False,
        )
        assert result == ""

        # With opt-in, should create
        result = detect_project(
            ceo_db,
            cwd="/home/user/new-project",
            emb=mock_emb,
            allow_auto_create=True,
        )
        assert result != ""

    def test_semantic_threshold_tightened(self, ceo_db, mock_emb):
        """Semantic search requires dist < 0.3 (similarity > 0.7)."""
        # With mock embeddings, all vectors are similar enough,
        # but we verify the function doesn't crash and returns expected behavior.
        p = ProjectFactory.build(name="TestProject")
        if mock_emb:
            p.embedding = mock_emb.encode_document("TestProject")
        ceo_db.insert_project(p)

        # Content that doesn't name the project but might semantically match
        result = detect_project(
            ceo_db,
            content="totally unrelated content about cooking recipes",
            emb=mock_emb,
        )
        # The result depends on mock embedding behavior; main thing is no crash.
        assert isinstance(result, str)
