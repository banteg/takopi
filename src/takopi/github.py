from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from .logging import get_logger
from .utils.git import git_stdout

logger = get_logger(__name__)


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GitHubIssue:
    number: int
    title: str
    labels: tuple[str, ...]
    state: str
    html_url: str


def parse_github_remote(repo_path: Path) -> tuple[str, str]:
    """Extract (owner, repo) from the git remote origin URL."""
    url = git_stdout(["remote", "get-url", "origin"], cwd=repo_path)
    if not url:
        raise GitHubError("no git remote 'origin' found")
    return _parse_owner_repo(url)


def _parse_owner_repo(url: str) -> tuple[str, str]:
    url = url.strip().removesuffix(".git")
    if url.startswith("git@"):
        # git@github.com:owner/repo
        _, _, path = url.partition(":")
    elif "github.com" in url:
        # https://github.com/owner/repo
        parts = url.split("github.com/", 1)
        if len(parts) < 2:
            raise GitHubError(f"cannot parse github remote: {url!r}")
        path = parts[1]
    else:
        raise GitHubError(f"cannot parse github remote: {url!r}")
    segments = path.strip("/").split("/")
    if len(segments) < 2:
        raise GitHubError(f"cannot parse owner/repo from: {url!r}")
    return segments[0], segments[1]


def _get_github_token() -> str | None:
    """Try GITHUB_TOKEN env var, then `gh auth token`."""
    import os
    import subprocess

    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token.strip()
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except FileNotFoundError:
        pass
    return None


async def fetch_issues(
    owner: str,
    repo: str,
    *,
    labels: list[str] | None = None,
    state: str = "open",
    limit: int = 30,
    token: str | None = None,
) -> list[GitHubIssue]:
    """Fetch all issues from a GitHub repository, applying *limit* locally."""
    if token is None:
        token = _get_github_token()

    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    params: dict[str, str | int] = {
        "state": state,
        "per_page": 100,
        "sort": "created",
        "direction": "desc",
    }
    if labels:
        params["labels"] = ",".join(labels)

    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    issues: list[GitHubIssue] = []

    async with httpx.AsyncClient(timeout=30) as client:
        page = 1
        while True:
            resp = await client.get(
                url, headers=headers, params={**params, "page": page}
            )
            if resp.status_code == 401:
                raise GitHubError(
                    "github authentication failed; set GITHUB_TOKEN or run `gh auth login`"
                )
            if resp.status_code == 404:
                raise GitHubError(f"repository {owner}/{repo} not found")
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break

            for item in data:
                # skip pull requests (they also appear in /issues)
                if "pull_request" in item:
                    continue
                issue_labels = tuple(
                    label["name"]
                    for label in item.get("labels", [])
                    if "name" in label
                )
                issues.append(
                    GitHubIssue(
                        number=item["number"],
                        title=item["title"],
                        labels=issue_labels,
                        state=item["state"],
                        html_url=item["html_url"],
                    )
                )

            if len(data) < 100:
                break
            page += 1

    return issues[:limit]


def issue_branch_name(issue_number: int) -> str:
    """Generate a branch name for a GitHub issue."""
    return f"issue/{issue_number}"
