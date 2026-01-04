from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from .model import EngineId, WorkspaceName

CommandType = Literal["new", "workspace", "workspaces", "sessions", "drop", "commit"]


@dataclass(frozen=True, slots=True)
class NewCommand:
    type: Literal["new"] = field(default="new", init=False)


@dataclass(frozen=True, slots=True)
class WorkspaceCommand:
    name: WorkspaceName
    type: Literal["workspace"] = field(default="workspace", init=False)


@dataclass(frozen=True, slots=True)
class WorkspacesCommand:
    type: Literal["workspaces"] = field(default="workspaces", init=False)


@dataclass(frozen=True, slots=True)
class SessionsCommand:
    type: Literal["sessions"] = field(default="sessions", init=False)


@dataclass(frozen=True, slots=True)
class DropCommand:
    engine: EngineId
    type: Literal["drop"] = field(default="drop", init=False)


@dataclass(frozen=True, slots=True)
class CommitCommand:
    message: str | None = None
    type: Literal["commit"] = field(default="commit", init=False)


DaemonCommand = (
    NewCommand
    | WorkspaceCommand
    | WorkspacesCommand
    | SessionsCommand
    | DropCommand
    | CommitCommand
)

_NEW_RE = re.compile(r"^/new(?:@\S+)?(?:\s|$)", re.IGNORECASE)
_WORKSPACE_RE = re.compile(r"^/workspace(?:@\S+)?\s+(\S+)", re.IGNORECASE)
_WORKSPACES_RE = re.compile(r"^/workspaces(?:@\S+)?(?:\s|$)", re.IGNORECASE)
_SESSIONS_RE = re.compile(r"^/sessions(?:@\S+)?(?:\s|$)", re.IGNORECASE)
_DROP_RE = re.compile(r"^/drop(?:@\S+)?\s+(\S+)", re.IGNORECASE)
_COMMIT_RE = re.compile(r"^/commit(?:@\S+)?(?:\s+(.+))?$", re.IGNORECASE)


def parse_daemon_command(text: str) -> DaemonCommand | None:
    stripped = text.strip()
    if not stripped:
        return None

    if _NEW_RE.match(stripped):
        return NewCommand()

    match = _WORKSPACE_RE.match(stripped)
    if match:
        return WorkspaceCommand(name=match.group(1))

    if _WORKSPACES_RE.match(stripped):
        return WorkspacesCommand()

    if _SESSIONS_RE.match(stripped):
        return SessionsCommand()

    match = _DROP_RE.match(stripped)
    if match:
        return DropCommand(engine=match.group(1))

    match = _COMMIT_RE.match(stripped)
    if match:
        message = match.group(1)
        return CommitCommand(message=message.strip() if message else None)

    return None


def is_daemon_command(text: str) -> bool:
    return parse_daemon_command(text) is not None


def strip_daemon_command(text: str) -> tuple[str, DaemonCommand | None]:
    stripped = text.strip()
    if not stripped:
        return text, None

    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if idx is None:
        return text, None

    line = lines[idx].strip()
    cmd = parse_daemon_command(line)
    if cmd is None:
        return text, None

    if isinstance(cmd, (NewCommand, WorkspacesCommand, SessionsCommand, CommitCommand)):
        lines.pop(idx)
        return "\n".join(lines).strip(), cmd

    if isinstance(cmd, WorkspaceCommand):
        match = _WORKSPACE_RE.match(line)
        if match:
            remainder = line[match.end() :].strip()
            if remainder:
                lines[idx] = remainder
            else:
                lines.pop(idx)
            return "\n".join(lines).strip(), cmd

    if isinstance(cmd, DropCommand):
        match = _DROP_RE.match(line)
        if match:
            remainder = line[match.end() :].strip()
            if remainder:
                lines[idx] = remainder
            else:
                lines.pop(idx)
            return "\n".join(lines).strip(), cmd

    return text, None
