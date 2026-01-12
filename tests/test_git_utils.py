from pathlib import Path

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
