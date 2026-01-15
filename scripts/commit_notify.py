# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "requests>=2.32.5",
# ]
# ///
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

repo = os.environ["REPO"]
bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
chat_id = os.environ["TELEGRAM_CHAT_ID"]

event_path = Path(os.environ["GITHUB_EVENT_PATH"])
event = json.loads(event_path.read_text(encoding="utf-8"))

ref = event.get("ref") or ""
branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ref
before = event.get("before") or ""
after = event.get("after") or ""
compare = event.get("compare") or ""

commits = list(event.get("commits") or [])
head_commit = event.get("head_commit")
if not commits and head_commit:
    commits = [head_commit]


def _short_sha(value: str) -> str:
    return value[:7] if value else ""


def _commit_line(commit: dict[str, object]) -> str:
    sha = _short_sha(str(commit.get("id") or ""))
    message = str(commit.get("message") or "").splitlines()[0].strip()
    url = str(commit.get("url") or commit.get("html_url") or "").strip()
    if url:
        return f"- {sha} {message} ({url})"
    return f"- {sha} {message}"


lines: list[str] = []
if commits:
    max_commits = 10
    lines.extend(_commit_line(commit) for commit in commits[:max_commits])
    if len(commits) > max_commits:
        lines.append(f"- ...and {len(commits) - max_commits} more")

header = f"push to {repo} {branch}".strip()
parts = [header]
if before and after and before != after:
    parts.append(f"range {_short_sha(before)}..{_short_sha(after)}")
if compare:
    parts.append(compare)
if lines:
    parts.append("\n".join(lines))

text = "\n\n".join(part for part in parts if part)

payload = {
    "chat_id": chat_id,
    "text": text,
    "link_preview_options": {"is_disabled": True},
}
resp = requests.post(
    f"https://api.telegram.org/bot{bot_token}/sendMessage", json=payload
)
resp.raise_for_status()
print(f"sent to {chat_id}")
