from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def _run_git(
    args: Sequence[str], *, cwd: Path
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        return None


def git_run(
    args: Sequence[str], *, cwd: Path
) -> subprocess.CompletedProcess[str] | None:
    return _run_git(args, cwd=cwd)


def git_stdout(args: Sequence[str], *, cwd: Path) -> str | None:
    result = _run_git(args, cwd=cwd)
    if result is None or result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def git_ok(args: Sequence[str], *, cwd: Path) -> bool:
    result = _run_git(args, cwd=cwd)
    return result is not None and result.returncode == 0


def git_is_worktree(path: Path) -> bool:
    top = git_stdout(
        ["rev-parse", "--path-format=absolute", "--show-toplevel"],
        cwd=path,
    )
    if not top:
        return False
    return Path(top).resolve(strict=False) == path.resolve(strict=False)


def resolve_default_base(root: Path) -> str | None:
    origin_head = git_stdout(
        ["symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
        cwd=root,
    )
    if origin_head:
        prefix = "refs/remotes/origin/"
        if origin_head.startswith(prefix):
            name = origin_head[len(prefix) :].strip()
            if name:
                return name

    current = git_stdout(["branch", "--show-current"], cwd=root)
    if current:
        return current

    if git_ok(["show-ref", "--verify", "--quiet", "refs/heads/master"], cwd=root):
        return "master"
    if git_ok(["show-ref", "--verify", "--quiet", "refs/heads/main"], cwd=root):
        return "main"
    return None


def resolve_main_worktree_root(cwd: Path) -> Path | None:
    common_dir = git_stdout(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=cwd,
    )
    if not common_dir:
        return None
    if git_stdout(["rev-parse", "--is-bare-repository"], cwd=cwd) == "true":
        return cwd
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = (cwd / common_path).resolve()
    return common_path.parent
