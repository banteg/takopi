from pathlib import Path
from types import SimpleNamespace

import pytest

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.worktrees import WorktreeError, _run_setup_script, ensure_worktree, resolve_run_cwd


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


def test_ensure_worktree_runs_setup_script_on_creation(
    monkeypatch, tmp_path: Path
) -> None:
    script_env: dict = {}
    project = ProjectConfig(
        alias="z80",
        path=tmp_path,
        worktrees_dir=Path(".worktrees"),
        worktree_setup_script="echo hello",
    )

    monkeypatch.setattr("takopi.worktrees.git_ok", lambda *args, **kwargs: False)
    monkeypatch.setattr("takopi.worktrees.resolve_default_base", lambda *_: "main")
    monkeypatch.setattr(
        "takopi.worktrees.git_run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    def _fake_run_setup_script(script, *, project_path, worktree_path, branch):
        script_env["script"] = script
        script_env["project_path"] = project_path
        script_env["worktree_path"] = worktree_path
        script_env["branch"] = branch

    monkeypatch.setattr("takopi.worktrees._run_setup_script", _fake_run_setup_script)

    worktree_path = ensure_worktree(project, "feat/x")
    assert script_env["script"] == "echo hello"
    assert script_env["project_path"] == tmp_path
    assert script_env["worktree_path"] == worktree_path
    assert script_env["branch"] == "feat/x"


def test_ensure_worktree_skips_setup_script_for_existing(
    monkeypatch, tmp_path: Path
) -> None:
    project = ProjectConfig(
        alias="z80",
        path=tmp_path,
        worktrees_dir=Path(".worktrees"),
        worktree_setup_script="echo hello",
    )
    worktree_path = tmp_path / ".worktrees" / "foo"
    worktree_path.mkdir(parents=True)

    monkeypatch.setattr("takopi.worktrees.git_is_worktree", lambda _: True)

    script_called = []
    monkeypatch.setattr(
        "takopi.worktrees._run_setup_script",
        lambda *args, **kwargs: script_called.append(True),
    )

    ensure_worktree(project, "foo")
    assert script_called == []


def test_run_setup_script_raises_on_nonzero(tmp_path: Path) -> None:
    with pytest.raises(WorktreeError, match="worktree setup script failed"):
        _run_setup_script(
            "exit 1",
            project_path=tmp_path,
            worktree_path=tmp_path / "wt",
            branch="feat/x",
        )


def test_run_setup_script_passes_env_vars(tmp_path: Path) -> None:
    out_file = tmp_path / "env.txt"
    script = f'echo "$TAKOPI_BRANCH:$TAKOPI_PROJECT_PATH:$TAKOPI_WORKTREE_PATH" > {out_file}'
    worktree_path = tmp_path / "wt"
    _run_setup_script(script, project_path=tmp_path, worktree_path=worktree_path, branch="my-branch")
    content = out_file.read_text().strip()
    assert content == f"my-branch:{tmp_path}:{worktree_path}"


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
