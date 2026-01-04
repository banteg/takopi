from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

from takopi.workspaces import (
    GitNotFoundError,
    WorkspaceError,
    WorkspaceInfo,
    WorkspaceStatus,
    _extract_repo_name,
    _get_default_branch,
    _is_git_url,
    _sanitize_branch_name,
    add_workspace,
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


class TestWorkspaceInfo:
    def test_worktree_path_property(self) -> None:
        info = WorkspaceInfo(
            name="test",
            path=Path("/workspaces/test"),
            repo_path=Path("/repos/test.git"),
            branch="main",
            remote_url="https://github.com/user/test.git",
        )
        assert info.worktree_path == Path("/workspaces/test")


class TestExceptions:
    def test_workspace_error(self) -> None:
        with pytest.raises(WorkspaceError):
            raise WorkspaceError("test error")

    def test_git_not_found_is_workspace_error(self) -> None:
        assert issubclass(GitNotFoundError, WorkspaceError)


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


def make_git_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    return CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


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

    def test_workspace_valid(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = get_workspace_info("myproject")

        assert result is not None
        assert result.name == "myproject"
        assert result.branch == "takopi/myproject"
        assert result.remote_url == "https://github.com/user/myproject.git"


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

    def test_uncommitted_changes_blocks_removal(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("main\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "status":
                return make_git_result("M file.txt\n")
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    with pytest.raises(WorkspaceError, match="uncommitted changes"):
                        remove_workspace("myproject")

    def test_force_removes_with_changes(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("main\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "worktree":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    remove_workspace("myproject", force=True)


class TestPullWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                pull_workspace("nonexistent")

    def test_pull_success(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote" and "get-url" in args:
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] == "fetch":
                return make_git_result()
            if args[0] == "rebase":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = pull_workspace("myproject")

        assert "Rebased" in result
        assert "main" in result


class TestPushWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                push_workspace("nonexistent")

    def test_push_success(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse":
                return make_git_result("takopi/myproject\n")
            if args[0] == "push":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces._run_git", mock_run_git):
                result = push_workspace("myproject")

        assert "Pushed" in result


class TestResetWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                reset_workspace("nonexistent")

    def test_hard_reset(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] in ("fetch", "reset", "clean"):
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = reset_workspace("myproject", hard=True)

        assert "hard" in result.lower()

    def test_soft_reset(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] in ("fetch", "reset"):
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = reset_workspace("myproject", hard=False)

        assert "soft" in result.lower()


class TestGetWorkspaceStatus:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                get_workspace_status("nonexistent")

    def test_status_clean(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] == "rev-list":
                return make_git_result("2\t1\n")
            if args[0] == "status":
                return make_git_result("")
            if args[0] == "fetch":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    status = get_workspace_status("myproject")

        assert status.name == "myproject"
        assert status.branch == "takopi/myproject"
        assert status.ahead == 2
        assert status.behind == 1
        assert status.dirty is False
        assert status.untracked == 0

    def test_status_dirty_with_untracked(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("main\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] == "rev-list":
                return make_git_result("0\t0\n")
            if args[0] == "status":
                return make_git_result("M file.txt\n?? newfile.txt\n?? another.txt\n")
            if args[0] == "fetch":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    status = get_workspace_status("myproject")

        assert status.dirty is True
        assert status.untracked == 2


class TestLinkWorkspace:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                link_workspace("nonexistent", "/some/path")

    def test_local_path_not_exists(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("main\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    with pytest.raises(WorkspaceError, match="does not exist"):
                        link_workspace("myproject", "/nonexistent/path")

    def test_local_path_not_git_repo(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        local_repo = tmp_path / "local"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        local_repo.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("main\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    with pytest.raises(WorkspaceError, match="Not a git repository"):
                        link_workspace("myproject", str(local_repo))

    def test_link_success(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        local_repo = tmp_path / "local"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        local_repo.mkdir()
        (local_repo / ".git").mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote" and args[1] == "get-url" and args[2] == "origin":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "remote" and args[1] == "get-url" and args[2] == "takopi":
                return make_git_result("", returncode=1)
            if args[0] == "remote" and args[1] == "add":
                return make_git_result()
            if args[0] == "fetch":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = link_workspace("myproject", str(local_repo))

        assert "Linked" in result


class TestGetWorkspaceLog:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                get_workspace_log("nonexistent")

    def test_log_with_commits(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] == "log":
                return make_git_result("abc123 Add feature\ndef456 Fix bug\n")
            if args[0] == "fetch":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = get_workspace_log("myproject")

        assert "abc123" in result
        assert "def456" in result

    def test_log_no_commits(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] == "log":
                return make_git_result("")
            if args[0] == "fetch":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = get_workspace_log("myproject")

        assert "No commits" in result


class TestGetWorkspaceDiff:
    def test_workspace_not_found(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with pytest.raises(WorkspaceError, match="not found"):
                get_workspace_diff("nonexistent")

    def test_diff_with_changes(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] == "diff":
                return make_git_result("diff --git a/file.py b/file.py\n+new line\n")
            if args[0] == "fetch":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = get_workspace_diff("myproject")

        assert "diff --git" in result

    def test_diff_no_changes(self, tmp_path: Path) -> None:
        workspaces_dir = tmp_path / "workspaces"
        repos_dir = tmp_path / "repos"
        workspaces_dir.mkdir()
        repos_dir.mkdir()
        ws_path = workspaces_dir / "myproject"
        ws_path.mkdir()
        (ws_path / ".git").touch()

        def mock_run_git(args, cwd=None, check=True):
            if args[0] == "rev-parse" and "--abbrev-ref" in args:
                return make_git_result("takopi/myproject\n")
            if args[0] == "rev-parse" and "--git-common-dir" in args:
                return make_git_result(str(repos_dir / "myproject.git") + "\n")
            if args[0] == "remote":
                return make_git_result("https://github.com/user/myproject.git\n")
            if args[0] == "ls-remote":
                return make_git_result("ref: refs/heads/main\tHEAD\n")
            if args[0] == "diff":
                return make_git_result("")
            if args[0] == "fetch":
                return make_git_result()
            return make_git_result()

        with patch("takopi.workspaces.WORKSPACES_DIR", workspaces_dir):
            with patch("takopi.workspaces.REPOS_DIR", repos_dir):
                with patch("takopi.workspaces._run_git", mock_run_git):
                    result = get_workspace_diff("myproject")

        assert "No diff" in result


class TestGetDefaultBranch:
    def test_parses_symref_output(self, tmp_path: Path) -> None:
        def mock_run_git(args, cwd=None, check=True):
            return make_git_result("ref: refs/heads/main\tHEAD\n")

        with patch("takopi.workspaces._run_git", mock_run_git):
            result = _get_default_branch(tmp_path)

        assert result == "main"

    def test_returns_none_on_failure(self, tmp_path: Path) -> None:
        def mock_run_git(args, cwd=None, check=True):
            return make_git_result("", returncode=1)

        with patch("takopi.workspaces._run_git", mock_run_git):
            result = _get_default_branch(tmp_path)

        assert result is None
