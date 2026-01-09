from __future__ import annotations

from pathlib import Path

from .config import ProjectConfig, ProjectsConfig
from .context import RunContext
from .utils.git import (
    git_is_worktree,
    git_ok,
    git_run,
    git_stdout,
    resolve_default_base,
)


class WorktreeError(RuntimeError):
    pass


def resolve_run_cwd(
    context: RunContext | None,
    *,
    projects: ProjectsConfig,
) -> Path | None:
    if context is None or context.project is None:
        return None
    project = projects.projects.get(context.project)
    if project is None:
        raise WorktreeError(f"unknown project {context.project!r}")
    if context.branch is None:
        return project.path
    branch = _sanitize_branch(context.branch)
    if _matches_project_branch(project.path, branch):
        return project.path
    return ensure_worktree(project, branch)


def ensure_worktree(project: ProjectConfig, branch: str) -> Path:
    root = project.path
    if not root.exists():
        raise WorktreeError(f"project path not found: {root}")

    branch = _sanitize_branch(branch)
    worktrees_root = project.worktrees_root
    worktree_path = worktrees_root / branch
    _ensure_within_root(worktrees_root, worktree_path)

    if worktree_path.exists():
        if not git_is_worktree(worktree_path):
            raise WorktreeError(f"{worktree_path} exists but is not a git worktree")
        return worktree_path

    worktrees_root.mkdir(parents=True, exist_ok=True)

    if git_ok(
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=root,
    ):
        _git_worktree_add(root, worktree_path, branch)
        return worktree_path

    if git_ok(
        ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
        cwd=root,
    ):
        _git_worktree_add(
            root,
            worktree_path,
            branch,
            base_ref=f"origin/{branch}",
            create_branch=True,
        )
        return worktree_path

    base = project.worktree_base or resolve_default_base(root)
    if not base:
        raise WorktreeError("cannot determine base branch for new worktree")

    _git_worktree_add(
        root,
        worktree_path,
        branch,
        base_ref=base,
        create_branch=True,
    )
    return worktree_path


def _git_worktree_add(
    root: Path,
    worktree_path: Path,
    branch: str,
    *,
    base_ref: str | None = None,
    create_branch: bool = False,
) -> None:
    if create_branch:
        if not base_ref:
            raise WorktreeError("missing base ref for worktree creation")
        args = ["worktree", "add", "-b", branch, str(worktree_path), base_ref]
    else:
        args = ["worktree", "add", str(worktree_path), branch]

    result = git_run(args, cwd=root)
    if result is None:
        raise WorktreeError("git not available on PATH")
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise WorktreeError(message or "git worktree add failed")


def _sanitize_branch(branch: str) -> str:
    cleaned = branch.strip()
    if not cleaned:
        raise WorktreeError("branch name cannot be empty")
    if cleaned.startswith("/"):
        raise WorktreeError("branch name cannot start with '/'")
    for part in Path(cleaned).parts:
        if part == "..":
            raise WorktreeError("branch name cannot contain '..'")
    return cleaned


def _matches_project_branch(root: Path, branch: str) -> bool:
    current = git_stdout(["branch", "--show-current"], cwd=root)
    if not current:
        return False
    return current == branch


def _ensure_within_root(root: Path, path: Path) -> None:
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    if not path_resolved.is_relative_to(root_resolved):
        raise WorktreeError("branch path escapes the worktrees directory")
