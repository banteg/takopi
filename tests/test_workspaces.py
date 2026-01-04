import subprocess
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from takopi.workspaces import (
    WorkspaceError,
    _extract_repo_name,
    _get_default_branch,
    _is_git_url,
    _sanitize_branch_name,
    ensure_directories,
    get_workspace_diff,
    get_workspace_info,
    get_workspace_log,
    get_workspace_status,
    link_workspace,
    list_workspaces,
    pull_workspace,
    push_workspace,
    remove_workspace,
    reset_workspace,
    workspace_exists,
)


def run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_workspace(tmp_path: Path):
    repos_dir = tmp_path / "repos"
    workspaces_dir = tmp_path / "workspaces"
    repos_dir.mkdir()
    workspaces_dir.mkdir()

    bare_repo = repos_dir / "myproject.git"
    bare_repo.mkdir()
    run_git(["init", "--bare"], bare_repo)

    temp_clone = tmp_path / "temp_clone"
    run_git(["clone", str(bare_repo), str(temp_clone)], tmp_path)
    run_git(["config", "user.email", "test@test.com"], temp_clone)
    run_git(["config", "user.name", "Test"], temp_clone)
    (temp_clone / "README.md").write_text("# Test Project\n")
    run_git(["add", "README.md"], temp_clone)
    run_git(["commit", "-m", "Initial commit"], temp_clone)
    run_git(["push", "origin", "main"], temp_clone)

    ws_path = workspaces_dir / "myproject"
    run_git(
        ["worktree", "add", "-b", "takopi/myproject", str(ws_path), "main"],
        bare_repo,
    )
    run_git(["remote", "add", "origin", str(bare_repo)], ws_path)
    run_git(["config", "user.email", "test@test.com"], ws_path)
    run_git(["config", "user.name", "Test"], ws_path)

    return {
        "repos_dir": repos_dir,
        "workspaces_dir": workspaces_dir,
        "bare_repo": bare_repo,
        "workspace_path": ws_path,
        "temp_clone": temp_clone,
    }


class TestIsGitUrl:
    def test_https_url(self) -> None:
        assert _is_git_url("https://github.com/user/repo.git")
        assert _is_git_url("https://github.com/user/repo")

    def test_git_at_url(self) -> None:
        assert _is_git_url("git@github.com:user/repo.git")

    def test_ends_with_git(self) -> None:
        assert _is_git_url("something.git")

    def test_local_path_not_url(self) -> None:
        assert not _is_git_url("/path/to/repo")
        assert not _is_git_url("./repo")


class TestExtractRepoName:
    def test_https_url_with_git(self) -> None:
        assert _extract_repo_name("https://github.com/user/myrepo.git") == "myrepo"

    def test_git_at_url(self) -> None:
        assert _extract_repo_name("git@github.com:user/myrepo.git") == "myrepo"

    def test_local_path(self) -> None:
        assert _extract_repo_name("/home/user/projects/myrepo") == "myrepo"


class TestSanitizeBranchName:
    def test_valid_name_unchanged(self) -> None:
        assert _sanitize_branch_name("feature-branch") == "feature-branch"

    def test_special_chars_replaced(self) -> None:
        assert _sanitize_branch_name("feature/branch") == "feature-branch"

    def test_leading_trailing_dashes_stripped(self) -> None:
        assert _sanitize_branch_name("-branch-") == "branch"


class TestEnsureDirectories:
    def test_creates_directories(self, tmp_path: Path) -> None:
        takopi_dir = tmp_path / ".takopi"
        repos_dir = takopi_dir / "repos"
        workspaces_dir = takopi_dir / "workspaces"

        with patch("takopi.workspaces.TAKOPI_DIR", takopi_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
                    ensure_directories()

        assert takopi_dir.exists()
        assert repos_dir.exists()
        assert workspaces_dir.exists()


class TestWorkspaceExists:
    def test_exists(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()
        (workspaces_dir / "myproject").mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            assert workspace_exists("myproject") is True

    def test_not_exists(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            assert workspace_exists("nonexistent") is False


class TestGetWorkspaceInfo:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            result = get_workspace_info("nonexistent")

        assert result is None

    def test_workspace_no_git_dir(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()
        (workspaces_dir / "myproject").mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            result = get_workspace_info("myproject")

        assert result is None

    def test_real_workspace(self, git_workspace) -> None:
        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = get_workspace_info("myproject")

        assert result is not None
        assert result.name == "myproject"
        assert result.branch == "takopi/myproject"
        assert result.path == git_workspace["workspace_path"]


class TestListWorkspaces:
    def test_no_workspaces_dir(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            result = list_workspaces()

        assert result == []

    def test_empty_workspaces(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            result = list_workspaces()

        assert result == []

    def test_real_workspaces(self, git_workspace) -> None:
        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = list_workspaces()

        assert len(result) == 1
        assert result[0].name == "myproject"


class TestGetWorkspaceStatus:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                get_workspace_status("nonexistent")

    def test_real_status_clean(self, git_workspace) -> None:
        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                status = get_workspace_status("myproject")

        assert status.name == "myproject"
        assert status.branch == "takopi/myproject"
        assert status.dirty is False
        assert status.untracked == 0

    def test_real_status_dirty(self, git_workspace) -> None:
        ws_path = git_workspace["workspace_path"]
        (ws_path / "newfile.txt").write_text("test content")
        (ws_path / "README.md").write_text("# Modified\n")

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                status = get_workspace_status("myproject")

        assert status.dirty is True
        assert status.untracked == 1


class TestRemoveWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                remove_workspace("nonexistent")

    def test_invalid_workspace(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()
        (workspaces_dir / "myproject").mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="Invalid workspace"):
                remove_workspace("myproject")

    def test_real_uncommitted_changes_blocks(self, git_workspace) -> None:
        ws_path = git_workspace["workspace_path"]
        (ws_path / "README.md").write_text("# Modified\n")

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                with pytest.raises(WorkspaceError, match="uncommitted changes"):
                    remove_workspace("myproject")

    def test_real_force_removes(self, git_workspace) -> None:
        ws_path = git_workspace["workspace_path"]
        (ws_path / "README.md").write_text("# Modified\n")

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                remove_workspace("myproject", force=True)

        assert not ws_path.exists()


class TestLinkWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                link_workspace("nonexistent", "/some/path")

    def test_local_path_not_exists(self, git_workspace) -> None:
        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                with pytest.raises(WorkspaceError, match="does not exist"):
                    link_workspace("myproject", "/nonexistent/path")

    def test_local_path_not_git_repo(self, git_workspace, tmp_path: Path) -> None:
        local_dir = tmp_path / "not_a_repo"
        local_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                with pytest.raises(WorkspaceError, match="Not a git repository"):
                    link_workspace("myproject", str(local_dir))

    def test_real_link_success(self, git_workspace) -> None:
        local_repo = git_workspace["temp_clone"]

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = link_workspace("myproject", str(local_repo))

        assert "Linked" in result or "Already linked" in result

        remotes = subprocess.run(
            ["git", "remote", "-v"], cwd=local_repo, capture_output=True, text=True
        )
        assert "takopi" in remotes.stdout


class TestGetWorkspaceLog:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                get_workspace_log("nonexistent")

    def test_real_log_no_commits_ahead(self, git_workspace) -> None:
        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = get_workspace_log("myproject")

        assert "No commits" in result

    def test_real_log_with_commits(self, git_workspace) -> None:
        ws_path = git_workspace["workspace_path"]
        (ws_path / "feature.txt").write_text("new feature\n")
        run_git(["add", "feature.txt"], ws_path)
        run_git(["commit", "-m", "Add feature"], ws_path)

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = get_workspace_log("myproject")

        assert "Add feature" in result


class TestGetWorkspaceDiff:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                get_workspace_diff("nonexistent")

    def test_real_diff_no_changes(self, git_workspace) -> None:
        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = get_workspace_diff("myproject")

        assert "No diff" in result

    def test_real_diff_with_changes(self, git_workspace) -> None:
        ws_path = git_workspace["workspace_path"]
        (ws_path / "feature.txt").write_text("new feature\n")
        run_git(["add", "feature.txt"], ws_path)
        run_git(["commit", "-m", "Add feature"], ws_path)

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = get_workspace_diff("myproject")

        assert "feature.txt" in result


class TestGetDefaultBranch:
    def test_real_default_branch(self, git_workspace) -> None:
        result = _get_default_branch(git_workspace["workspace_path"])
        assert result == "main"


class TestPullWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                pull_workspace("nonexistent")


class TestPushWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                push_workspace("nonexistent")


class TestResetWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                reset_workspace("nonexistent")

    def test_real_hard_reset(self, git_workspace) -> None:
        ws_path = git_workspace["workspace_path"]
        (ws_path / "README.md").write_text("# Modified\n")
        (ws_path / "newfile.txt").write_text("untracked\n")

        with patch("takopi.workspaces.WORKSPACES_DIR", git_workspace["workspaces_dir"]):
            with patch("takopi.workspaces.REPOS_DIR", git_workspace["repos_dir"]):
                result = reset_workspace("myproject", hard=True)

        assert "hard" in result.lower()
        assert (ws_path / "README.md").read_text() == "# Test Project\n"
        assert not (ws_path / "newfile.txt").exists()
