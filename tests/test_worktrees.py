from pathlib import Path
from types import SimpleNamespace

import pytest

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.worktrees import WorktreeError, ensure_worktree, resolve_run_cwd


def _projects_config(path: Path) -> ProjectsConfig:
    return ProjectsConfig(
        projects={
            "z80": ProjectConfig(
                alias="z80",
                path=path,
                worktrees_dir=Path(".worktrees"),
            )
        },
        default_project=None,
    )


def test_resolve_run_cwd_uses_project_root(tmp_path: Path) -> None:
    projects = _projects_config(tmp_path)
    ctx = RunContext(project="z80")
    assert resolve_run_cwd(ctx, projects=projects) == tmp_path


def test_resolve_run_cwd_rejects_invalid_branch(tmp_path: Path) -> None:
    projects = _projects_config(tmp_path)
    ctx = RunContext(project="z80", branch="../oops")
    with pytest.raises(WorktreeError, match="branch name"):
        resolve_run_cwd(ctx, projects=projects)


def test_resolve_run_cwd_uses_root_when_branch_matches(
    monkeypatch, tmp_path: Path
) -> None:
    projects = _projects_config(tmp_path)

    def _fake_stdout(args, **_kwargs):
        if args == ["branch", "--show-current"]:
            return "main"
        return None

    def _unexpected(*_args, **_kwargs):
        raise AssertionError("unexpected")

    monkeypatch.setattr("takopi.worktrees.git_stdout", _fake_stdout)
    monkeypatch.setattr(
        "takopi.worktrees.ensure_worktree",
        _unexpected,
    )

    ctx = RunContext(project="z80", branch="main")
    assert resolve_run_cwd(ctx, projects=projects) == tmp_path


def test_ensure_worktree_creates_from_base(monkeypatch, tmp_path: Path) -> None:
    project = ProjectConfig(
        alias="z80",
        path=tmp_path,
        worktrees_dir=Path(".worktrees"),
    )
    calls: list[list[str]] = []

    monkeypatch.setattr("takopi.worktrees.git_ok", lambda *args, **kwargs: False)
    monkeypatch.setattr("takopi.worktrees.resolve_default_base", lambda *_: "main")

    def _fake_git_run(args, cwd):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("takopi.worktrees.git_run", _fake_git_run)

    worktree_path = ensure_worktree(project, "feat/name")
    assert worktree_path == tmp_path / ".worktrees" / "feat" / "name"
    assert calls == [["worktree", "add", "-b", "feat/name", str(worktree_path), "main"]]


def test_ensure_worktree_rejects_existing_non_worktree(
    monkeypatch, tmp_path: Path
) -> None:
    project = ProjectConfig(
        alias="z80",
        path=tmp_path,
        worktrees_dir=Path(".worktrees"),
    )
    worktree_path = tmp_path / ".worktrees" / "foo"
    worktree_path.mkdir(parents=True)

    def _fake_stdout(args, **kwargs):
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return "true"
        if args == ["rev-parse", "--path-format=absolute", "--show-toplevel"]:
            return str(tmp_path)
        return None

    monkeypatch.setattr("takopi.utils.git.git_stdout", _fake_stdout)

    with pytest.raises(WorktreeError, match="exists but is not a git worktree"):
        ensure_worktree(project, "foo")
