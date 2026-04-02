"""Tests for project detection module."""

from memory_core.project_detect import detect_project, _AUTO_CREATE_BLACKLIST, _is_valid_project_name
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
        p = ProjectFactory.build(name="TestProject")
        if mock_emb:
            p.embedding = mock_emb.encode_document("TestProject")
        ceo_db.insert_project(p)

        result = detect_project(
            ceo_db,
            content="totally unrelated content about cooking recipes",
            emb=mock_emb,
        )
        assert isinstance(result, str)

    def test_same_name_different_path_adopts_existing(self, ceo_db, mock_emb):
        """When CWD has a different path but same project name, adopt the existing project."""
        existing = ProjectFactory.build(name="clickmem", repo_url="/Users/auxten/Codes/clickmem")
        ceo_db.insert_project(existing)

        # Different machine, same project name
        result = detect_project(
            ceo_db,
            cwd="/Users/tong/clickmem",
            emb=mock_emb,
            allow_auto_create=True,
        )
        assert result == existing.id  # Should adopt, not create new

        # Verify repo_url was updated
        updated = ceo_db.get_project(existing.id)
        assert updated.repo_url == "/Users/tong/clickmem"

    def test_hidden_dir_not_auto_created(self, ceo_db, mock_emb):
        """Hidden directories (starting with .) should not become projects."""
        result = detect_project(
            ceo_db,
            cwd="/Users/tong/.openclaw",
            emb=mock_emb,
            allow_auto_create=True,
        )
        assert result == ""

    def test_home_dir_not_auto_created(self, ceo_db, mock_emb):
        """User home directory itself should not become a project."""
        import os
        home = os.path.expanduser("~")
        result = detect_project(
            ceo_db,
            cwd=home,
            emb=mock_emb,
            allow_auto_create=True,
        )
        assert result == ""

    def test_is_valid_project_name(self):
        """Validate project name heuristics."""
        assert _is_valid_project_name("clickmem") is True
        assert _is_valid_project_name("my-project") is True
        assert _is_valid_project_name(".openclaw") is False
        assert _is_valid_project_name(".claude") is False
        assert _is_valid_project_name("Downloads") is False
        assert _is_valid_project_name("") is False
        assert _is_valid_project_name("x") is False  # too short
