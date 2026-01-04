from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

TAKOPI_DIR = Path.home() / ".takopi"
REPOS_DIR = TAKOPI_DIR / "repos"
WORKSPACES_DIR = TAKOPI_DIR / "workspaces"

DEFAULT_BRANCH_PREFIX = "takopi"

_git_checked = False


class WorkspaceError(Exception):
    pass


class GitNotFoundError(WorkspaceError):
    pass


def _ensure_git_available() -> None:
    global _git_checked
    if _git_checked:
        return
    if shutil.which("git") is None:
        raise GitNotFoundError(
            "git is not installed or not in PATH.\n"
            "Workspace management requires git. Install it with:\n"
            "  - macOS: xcode-select --install\n"
            "  - Ubuntu/Debian: sudo apt install git\n"
            "  - Or download from https://git-scm.com/"
        )
    _git_checked = True


@dataclass(frozen=True, slots=True)
class WorkspaceInfo:
    name: str
    path: Path
    repo_path: Path
    branch: str
    remote_url: str

    @property
    def worktree_path(self) -> Path:
        return self.path


def _run_git(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    _ensure_git_available()
    cmd = ["git", *args]
    logger.debug("[git] %s (cwd=%s)", " ".join(cmd), cwd)
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise WorkspaceError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result


def _is_git_url(source: str) -> bool:
    if source.startswith(("git@", "https://", "http://", "ssh://", "git://")):
        return True
    if source.endswith(".git"):
        return True
    return False


def _extract_repo_name(source: str) -> str:
    if _is_git_url(source):
        path = source
        if source.startswith("git@"):
            path = source.split(":")[-1]
        else:
            parsed = urlparse(source)
            path = parsed.path

        name = Path(path).stem
        if name.endswith(".git"):
            name = name[:-4]
        return name

    return Path(source).resolve().name


def _get_remote_url(repo_path: Path) -> str | None:
    result = _run_git(["remote", "get-url", "origin"], cwd=repo_path, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _sanitize_branch_name(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    return sanitized.strip("-")


def ensure_directories() -> None:
    TAKOPI_DIR.mkdir(parents=True, exist_ok=True)
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)


def workspace_exists(name: str) -> bool:
    return (WORKSPACES_DIR / name).exists()


def get_workspace_info(name: str) -> WorkspaceInfo | None:
    workspace_path = WORKSPACES_DIR / name
    if not workspace_path.exists():
        return None

    git_file = workspace_path / ".git"
    if not git_file.exists():
        return None

    result = _run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_path, check=False
    )
    branch = result.stdout.strip() if result.returncode == 0 else "unknown"

    result = _run_git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=workspace_path,
        check=False,
    )
    repo_path = (
        Path(result.stdout.strip())
        if result.returncode == 0
        else REPOS_DIR / f"{name}.git"
    )

    remote_url = _get_remote_url(workspace_path) or ""

    return WorkspaceInfo(
        name=name,
        path=workspace_path,
        repo_path=repo_path,
        branch=branch,
        remote_url=remote_url,
    )


def list_workspaces() -> list[WorkspaceInfo]:
    if not WORKSPACES_DIR.exists():
        return []

    workspaces = []
    for path in WORKSPACES_DIR.iterdir():
        if path.is_dir():
            info = get_workspace_info(path.name)
            if info:
                workspaces.append(info)
    return sorted(workspaces, key=lambda w: w.name)


def add_workspace(
    source: str,
    name: str | None = None,
    branch: str | None = None,
) -> WorkspaceInfo:
    ensure_directories()

    if _is_git_url(source):
        remote_url = source
    else:
        local_path = Path(source).expanduser().resolve()
        if not local_path.exists():
            raise WorkspaceError(f"Path does not exist: {local_path}")
        if not (local_path / ".git").exists():
            raise WorkspaceError(f"Not a git repository: {local_path}")

        remote_url = _get_remote_url(local_path)
        if not remote_url:
            raise WorkspaceError(
                f"Repository has no remote 'origin' configured: {local_path}\n"
                "Please push to a remote first, or provide a URL directly."
            )

    workspace_name = name or _extract_repo_name(source)
    workspace_name = _sanitize_branch_name(workspace_name)

    if workspace_exists(workspace_name):
        raise WorkspaceError(f"Workspace already exists: {workspace_name}")

    bare_repo_path = REPOS_DIR / f"{workspace_name}.git"
    workspace_path = WORKSPACES_DIR / workspace_name
    worktree_branch = branch or f"{DEFAULT_BRANCH_PREFIX}/{workspace_name}"

    if not bare_repo_path.exists():
        logger.info("[workspace] cloning %s to %s", remote_url, bare_repo_path)
        _run_git(["clone", "--bare", remote_url, str(bare_repo_path)])

    _run_git(["fetch", "origin"], cwd=bare_repo_path)

    result = _run_git(
        ["rev-parse", "--verify", f"refs/heads/{worktree_branch}"],
        cwd=bare_repo_path,
        check=False,
    )
    branch_exists = result.returncode == 0

    if branch_exists:
        _run_git(
            ["worktree", "add", str(workspace_path), worktree_branch],
            cwd=bare_repo_path,
        )
    else:
        # For bare repos, find the default branch from HEAD
        default_branch_result = _run_git(
            ["symbolic-ref", "HEAD"],
            cwd=bare_repo_path,
            check=False,
        )
        if default_branch_result.returncode == 0:
            # refs/heads/main -> main
            default_ref = default_branch_result.stdout.strip()
            default_branch = default_ref.replace("refs/heads/", "")
        else:
            default_branch = "main"

        _run_git(
            [
                "worktree",
                "add",
                "-b",
                worktree_branch,
                str(workspace_path),
                default_branch,
            ],
            cwd=bare_repo_path,
        )

    logger.info(
        "[workspace] created workspace %s at %s", workspace_name, workspace_path
    )

    return WorkspaceInfo(
        name=workspace_name,
        path=workspace_path,
        repo_path=bare_repo_path,
        branch=worktree_branch,
        remote_url=remote_url,
    )


def remove_workspace(name: str, force: bool = False) -> None:
    workspace_path = WORKSPACES_DIR / name
    if not workspace_path.exists():
        raise WorkspaceError(f"Workspace not found: {name}")

    info = get_workspace_info(name)
    if not info:
        raise WorkspaceError(f"Invalid workspace: {name}")

    if not force:
        result = _run_git(["status", "--porcelain"], cwd=workspace_path, check=False)
        if result.stdout.strip():
            raise WorkspaceError(
                f"Workspace has uncommitted changes: {name}\n"
                "Use --force to remove anyway."
            )

    _run_git(["worktree", "remove", str(workspace_path), "--force"], cwd=info.repo_path)
    logger.info("[workspace] removed workspace %s", name)


def pull_workspace(name: str) -> str:
    workspace_path = WORKSPACES_DIR / name
    if not workspace_path.exists():
        raise WorkspaceError(f"Workspace not found: {name}")

    _run_git(["fetch", "origin"], cwd=workspace_path)

    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_path)
    current_branch = result.stdout.strip()

    upstream_result = _run_git(
        ["rev-parse", "--abbrev-ref", f"{current_branch}@{{upstream}}"],
        cwd=workspace_path,
        check=False,
    )

    if upstream_result.returncode == 0:
        _run_git(["pull", "--rebase"], cwd=workspace_path)
        return f"Pulled and rebased {name} on {current_branch}"
    else:
        result = _run_git(
            ["symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=workspace_path,
            check=False,
        )
        if result.returncode == 0:
            default_ref = result.stdout.strip()
            default_branch = default_ref.split("/")[-1]
            _run_git(["rebase", f"origin/{default_branch}"], cwd=workspace_path)
            return f"Rebased {name} ({current_branch}) onto origin/{default_branch}"

        return f"Fetched {name}, but no upstream configured for {current_branch}"


def push_workspace(name: str, set_upstream: bool = True) -> str:
    workspace_path = WORKSPACES_DIR / name
    if not workspace_path.exists():
        raise WorkspaceError(f"Workspace not found: {name}")

    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_path)
    current_branch = result.stdout.strip()

    push_args = ["push", "origin", current_branch]
    if set_upstream:
        push_args.insert(1, "-u")

    _run_git(push_args, cwd=workspace_path)
    return f"Pushed {name} ({current_branch}) to origin"


def reset_workspace(name: str, hard: bool = True) -> str:
    workspace_path = WORKSPACES_DIR / name
    if not workspace_path.exists():
        raise WorkspaceError(f"Workspace not found: {name}")

    _run_git(["fetch", "origin"], cwd=workspace_path)

    result = _run_git(
        ["symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=workspace_path,
        check=False,
    )
    if result.returncode == 0:
        default_ref = result.stdout.strip()
        default_branch = default_ref.split("/")[-1]
        target = f"origin/{default_branch}"
    else:
        target = "origin/main"

    if hard:
        _run_git(["reset", "--hard", target], cwd=workspace_path)
        _run_git(["clean", "-fd"], cwd=workspace_path)
        return f"Reset {name} to {target} (hard)"
    else:
        _run_git(["reset", target], cwd=workspace_path)
        return f"Reset {name} to {target} (soft)"


@dataclass(frozen=True, slots=True)
class WorkspaceStatus:
    name: str
    branch: str
    ahead: int
    behind: int
    dirty: bool
    untracked: int


def get_workspace_status(name: str) -> WorkspaceStatus:
    workspace_path = WORKSPACES_DIR / name
    if not workspace_path.exists():
        raise WorkspaceError(f"Workspace not found: {name}")

    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workspace_path)
    branch = result.stdout.strip()

    _run_git(["fetch", "origin"], cwd=workspace_path, check=False)

    ahead = 0
    behind = 0
    rev_list_result = _run_git(
        ["rev-list", "--left-right", "--count", f"{branch}...origin/main"],
        cwd=workspace_path,
        check=False,
    )
    if rev_list_result.returncode == 0:
        parts = rev_list_result.stdout.strip().split()
        if len(parts) == 2:
            ahead = int(parts[0])
            behind = int(parts[1])

    status_result = _run_git(["status", "--porcelain"], cwd=workspace_path)
    lines = [l for l in status_result.stdout.strip().split("\n") if l]
    dirty = any(not l.startswith("??") for l in lines)
    untracked = sum(1 for l in lines if l.startswith("??"))

    return WorkspaceStatus(
        name=name,
        branch=branch,
        ahead=ahead,
        behind=behind,
        dirty=dirty,
        untracked=untracked,
    )
