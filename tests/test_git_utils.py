from pathlib import Path
import subprocess

from takopi.utils.git import git_is_worktree, git_ok, git_run, git_stdout
from takopi.utils.git import resolve_default_base, resolve_main_worktree_root


def test_resolve_main_worktree_root_returns_none_when_no_git(monkeypatch) -> None:
    monkeypatch.setattr("takopi.utils.git.git_stdout", lambda *args, **kwargs: None)
    assert resolve_main_worktree_root(Path("/tmp")) is None


def test_resolve_main_worktree_root_prefers_common_dir_parent(monkeypatch) -> None:
    base = Path("/repo")

    def _fake_stdout(args, **kwargs):
        if args[:2] == ["rev-parse", "--path-format=absolute"]:
            return str(base / ".git")
        if args == ["rev-parse", "--is-bare-repository"]:
            return "false"
        return None

    monkeypatch.setattr("takopi.utils.git.git_stdout", _fake_stdout)
    assert resolve_main_worktree_root(base / ".worktrees" / "feature") == base


def test_resolve_main_worktree_root_returns_cwd_for_bare_repo(monkeypatch) -> None:
    cwd = Path("/bare-repo")

    def _fake_stdout(args, **kwargs):
        if args[:2] == ["rev-parse", "--path-format=absolute"]:
            return str(cwd / "repo.git")
        if args == ["rev-parse", "--is-bare-repository"]:
            return "true"
        return None

    monkeypatch.setattr("takopi.utils.git.git_stdout", _fake_stdout)
    assert resolve_main_worktree_root(cwd) == cwd


def test_resolve_default_base_prefers_master_over_main(monkeypatch) -> None:
    def _fake_stdout(args, **kwargs):
        if args[:2] == ["symbolic-ref", "-q"]:
            return None
        if args == ["branch", "--show-current"]:
            return None
        return None

    def _fake_ok(args, **kwargs):
        return args in (
            ["show-ref", "--verify", "--quiet", "refs/heads/master"],
            ["show-ref", "--verify", "--quiet", "refs/heads/main"],
        )

    monkeypatch.setattr("takopi.utils.git.git_stdout", _fake_stdout)
    monkeypatch.setattr("takopi.utils.git.git_ok", _fake_ok)
    assert resolve_default_base(Path("/repo")) == "master"


def test_resolve_default_base_uses_origin_head(monkeypatch) -> None:
    def _fake_stdout(args, **kwargs):
        if args[:2] == ["symbolic-ref", "-q"]:
            return "refs/remotes/origin/main"
        return None

    monkeypatch.setattr("takopi.utils.git.git_stdout", _fake_stdout)
    assert resolve_default_base(Path("/repo")) == "main"


def test_resolve_default_base_uses_current_branch(monkeypatch) -> None:
    def _fake_stdout(args, **kwargs):
        if args[:2] == ["symbolic-ref", "-q"]:
            return None
        if args == ["branch", "--show-current"]:
            return "feature"
        return None

    monkeypatch.setattr("takopi.utils.git.git_stdout", _fake_stdout)
    assert resolve_default_base(Path("/repo")) == "feature"


def test_git_run_handles_missing_git(monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr("takopi.utils.git.subprocess.run", _raise)
    assert git_run(["status"], cwd=Path("/repo")) is None


def test_git_stdout_returns_none_on_error(monkeypatch) -> None:
    def _fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["git"],
            returncode=1,
            stdout="oops",
            stderr="",
        )

    monkeypatch.setattr("takopi.utils.git._run_git", _fake_run)
    assert git_stdout(["status"], cwd=Path("/repo")) is None


def test_git_ok_false_when_run_missing(monkeypatch) -> None:
    monkeypatch.setattr("takopi.utils.git._run_git", lambda *_args, **_kwargs: None)
    assert git_ok(["status"], cwd=Path("/repo")) is False


def test_git_stdout_returns_trimmed_output(monkeypatch) -> None:
    def _fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="  ok \n",
            stderr="",
        )

    monkeypatch.setattr("takopi.utils.git._run_git", _fake_run)
    assert git_stdout(["status"], cwd=Path("/repo")) == "ok"


def test_git_is_worktree_false_when_no_top(monkeypatch) -> None:
    monkeypatch.setattr("takopi.utils.git.git_stdout", lambda *_a, **_k: None)
    assert git_is_worktree(Path("/repo")) is False


def test_git_is_worktree_matches_path(monkeypatch) -> None:
    monkeypatch.setattr(
        "takopi.utils.git.git_stdout",
        lambda *_a, **_k: "/repo",
    )
    assert git_is_worktree(Path("/repo")) is True
