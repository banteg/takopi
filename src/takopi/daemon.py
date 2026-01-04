from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .commands import (
    CommitCommand,
    DaemonCommand,
    DropCommand,
    NewCommand,
    SessionsCommand,
    WorkspaceCommand,
    WorkspacesCommand,
    parse_daemon_command,
    strip_daemon_command,
)
from .model import EngineId, ResumeToken, Workspace, WorkspaceName
from .workspaces import list_workspaces
from .telegram import (
    BotClient,
    make_workspace_keyboard,
    parse_workspace_callback,
)

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path.home() / ".takopi" / "daemon_state.json"


@dataclass
class WorkspaceSession:
    engine_sessions: dict[EngineId, ResumeToken] = field(default_factory=dict)
    active_engine: EngineId | None = None

    def get_resume_token(self, engine: EngineId) -> ResumeToken | None:
        return self.engine_sessions.get(engine)

    def set_resume_token(self, engine: EngineId, token: ResumeToken) -> None:
        self.engine_sessions[engine] = token
        self.active_engine = engine

    def clear_engine(self, engine: EngineId) -> None:
        self.engine_sessions.pop(engine, None)
        if self.active_engine == engine:
            self.active_engine = None

    def clear_all(self) -> None:
        self.engine_sessions.clear()
        self.active_engine = None

    def has_sessions(self) -> bool:
        return bool(self.engine_sessions)

    def session_count(self) -> int:
        return len(self.engine_sessions)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.engine_sessions:
            result["engine_sessions"] = {
                engine: {"engine": token.engine, "value": token.value}
                for engine, token in self.engine_sessions.items()
            }
        if self.active_engine is not None:
            result["active_engine"] = self.active_engine
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceSession:
        engine_sessions: dict[EngineId, ResumeToken] = {}
        for engine, token_data in data.get("engine_sessions", {}).items():
            engine_sessions[engine] = ResumeToken(
                engine=token_data["engine"], value=token_data["value"]
            )
        return cls(
            engine_sessions=engine_sessions,
            active_engine=data.get("active_engine"),
        )


@dataclass
class DaemonState:
    active_workspace: WorkspaceName | None = None
    workspace_sessions: dict[WorkspaceName, WorkspaceSession] = field(
        default_factory=dict
    )
    _path: Path | None = field(default=None, repr=False, compare=False)

    def set_active_workspace(self, name: WorkspaceName) -> None:
        self.active_workspace = name
        self._save()

    def get_session(self, workspace: WorkspaceName) -> WorkspaceSession:
        if workspace not in self.workspace_sessions:
            self.workspace_sessions[workspace] = WorkspaceSession()
        return self.workspace_sessions[workspace]

    def update_session(
        self,
        workspace: WorkspaceName,
        engine: EngineId,
        token: ResumeToken,
    ) -> None:
        session = self.get_session(workspace)
        session.set_resume_token(engine, token)
        self._save()

    def set_active_engine(self, workspace: WorkspaceName, engine: EngineId) -> None:
        session = self.get_session(workspace)
        session.active_engine = engine
        self._save()

    def get_engine_session(
        self, workspace: WorkspaceName, engine: EngineId
    ) -> ResumeToken | None:
        session = self.get_session(workspace)
        return session.get_resume_token(engine)

    def clear_engine_session(self, workspace: WorkspaceName, engine: EngineId) -> None:
        session = self.get_session(workspace)
        session.clear_engine(engine)
        self._save()

    def clear_all_sessions(self, workspace: WorkspaceName) -> None:
        if workspace in self.workspace_sessions:
            self.workspace_sessions[workspace] = WorkspaceSession()
            self._save()

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_workspace": self.active_workspace,
            "workspace_sessions": {
                name: session.to_dict()
                for name, session in self.workspace_sessions.items()
            },
        }

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self.to_dict(), indent=2), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("[daemon] failed to save state to %s: %s", self._path, e)

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path | None = None) -> DaemonState:
        workspace_sessions: dict[WorkspaceName, WorkspaceSession] = {}
        raw_sessions = data.get("workspace_sessions", {})
        if isinstance(raw_sessions, dict):
            for name, session_data in raw_sessions.items():
                workspace_sessions[name] = WorkspaceSession.from_dict(session_data)
        return cls(
            active_workspace=data.get("active_workspace"),
            workspace_sessions=workspace_sessions,
            _path=path,
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_STATE_PATH) -> DaemonState:
        if not path.is_file():
            logger.debug("[daemon] no state file at %s, starting fresh", path)
            return cls(_path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.warning(
                    "[daemon] state file %s has invalid format, starting fresh", path
                )
                return cls(_path=path)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                "[daemon] failed to load state from %s: %s, starting fresh", path, e
            )
            return cls(_path=path)
        state = cls.from_dict(data, path=path)
        logger.debug("[daemon] loaded state from %s", path)
        return state

    def save(self) -> None:
        self._save()


@dataclass
class DaemonConfig:
    workspaces: dict[WorkspaceName, Workspace]
    state: DaemonState
    default_workspace: WorkspaceName | None = None
    config_workspaces: dict[WorkspaceName, Workspace] = field(default_factory=dict)

    def workspace_names(self) -> list[WorkspaceName]:
        return list(self.workspaces.keys())

    def get_workspace(self, name: WorkspaceName) -> Workspace | None:
        return self.workspaces.get(name)

    def get_active_workspace(self) -> Workspace | None:
        if self.state.active_workspace is None:
            return None
        return self.get_workspace(self.state.active_workspace)

    def get_effective_workspace(self) -> Workspace | None:
        active = self.get_active_workspace()
        if active is not None:
            return active
        if self.default_workspace is not None:
            return self.get_workspace(self.default_workspace)
        names = self.workspace_names()
        if len(names) == 1:
            return self.get_workspace(names[0])
        return None

    def refresh_workspaces(self) -> None:
        discovered = {
            ws.name: Workspace(name=ws.name, path=ws.path) for ws in list_workspaces()
        }
        self.workspaces = {**discovered, **self.config_workspaces}


def _commit_workspace_changes(workspace_path: Path, message: str | None) -> str:
    import subprocess

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        return f"Error checking status: {status.stderr.strip()}"

    if not status.stdout.strip():
        return "No changes to commit."

    add_result = subprocess.run(
        ["git", "add", "-A"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
    )
    if add_result.returncode != 0:
        return f"Error staging changes: {add_result.stderr.strip()}"

    if not message:
        diff_stat = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
        )
        files_changed = len(
            [l for l in diff_stat.stdout.strip().split("\n") if l.strip()]
        )
        message = f"Update {files_changed} file(s)"

    commit_result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=workspace_path,
        capture_output=True,
        text=True,
    )
    if commit_result.returncode != 0:
        return f"Error committing: {commit_result.stderr.strip()}"

    rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=workspace_path,
        capture_output=True,
        text=True,
    )
    short_hash = rev.stdout.strip() if rev.returncode == 0 else "unknown"

    return f"Committed `{short_hash}`: {message}"


@dataclass(frozen=True, slots=True)
class CommandResult:
    handled: bool
    response_text: str | None = None
    keyboard: dict[str, Any] | None = None
    switch_workspace: WorkspaceName | None = None
    clear_session: bool = False


async def handle_daemon_command(
    cmd: DaemonCommand,
    daemon_cfg: DaemonConfig,
    bot: BotClient,
    chat_id: int,
    message_id: int,
) -> CommandResult:
    if isinstance(cmd, NewCommand):
        workspace = daemon_cfg.get_effective_workspace()
        if workspace is None:
            return CommandResult(
                handled=True,
                response_text="No active workspace. Use /workspaces to select one.",
            )
        daemon_cfg.state.clear_all_sessions(workspace.name)
        return CommandResult(
            handled=True,
            response_text=f"Starting new session in `{workspace.name}`",
            clear_session=True,
        )

    if isinstance(cmd, WorkspaceCommand):
        workspace = daemon_cfg.get_workspace(cmd.name)
        if workspace is None:
            daemon_cfg.refresh_workspaces()
            workspace = daemon_cfg.get_workspace(cmd.name)
        if workspace is None:
            available = ", ".join(daemon_cfg.workspace_names())
            return CommandResult(
                handled=True,
                response_text=f"Unknown workspace `{cmd.name}`. Available: {available}",
            )
        daemon_cfg.state.set_active_workspace(cmd.name)
        session = daemon_cfg.state.get_session(cmd.name)
        if session.has_sessions():
            engines = ", ".join(session.engine_sessions.keys())
            return CommandResult(
                handled=True,
                response_text=f"Switched to `{cmd.name}` (sessions: {engines})",
                switch_workspace=cmd.name,
            )
        return CommandResult(
            handled=True,
            response_text=f"Switched to `{cmd.name}` (no active sessions)",
            switch_workspace=cmd.name,
        )

    if isinstance(cmd, WorkspacesCommand):
        daemon_cfg.refresh_workspaces()
        names = daemon_cfg.workspace_names()
        if not names:
            return CommandResult(
                handled=True,
                response_text="No workspaces configured.",
            )
        keyboard = make_workspace_keyboard(names)
        active = daemon_cfg.state.active_workspace
        header = "Select a workspace:"
        if active:
            header = f"Current: `{active}`\n\nSelect a workspace:"
        return CommandResult(
            handled=True,
            response_text=header,
            keyboard=keyboard,
        )

    if isinstance(cmd, SessionsCommand):
        workspace = daemon_cfg.get_effective_workspace()
        if workspace is None:
            return CommandResult(
                handled=True,
                response_text="No active workspace. Use /workspaces to select one.",
            )
        session = daemon_cfg.state.get_session(workspace.name)
        if not session.has_sessions():
            return CommandResult(
                handled=True,
                response_text=f"`{workspace.name}` has no active sessions.",
            )
        lines = [f"Sessions in `{workspace.name}`:"]
        for engine, token in session.engine_sessions.items():
            marker = "→" if engine == session.active_engine else "•"
            lines.append(f"  {marker} `{engine}`: `{token.value[:20]}...`")
        return CommandResult(
            handled=True,
            response_text="\n".join(lines),
        )

    if isinstance(cmd, DropCommand):
        workspace = daemon_cfg.get_effective_workspace()
        if workspace is None:
            return CommandResult(
                handled=True,
                response_text="No active workspace. Use /workspaces to select one.",
            )
        session = daemon_cfg.state.get_session(workspace.name)
        if session.get_resume_token(cmd.engine) is None:
            return CommandResult(
                handled=True,
                response_text=f"No `{cmd.engine}` session in `{workspace.name}`.",
            )
        daemon_cfg.state.clear_engine_session(workspace.name, cmd.engine)
        return CommandResult(
            handled=True,
            response_text=f"Dropped `{cmd.engine}` session in `{workspace.name}`.",
        )

    if isinstance(cmd, CommitCommand):
        workspace = daemon_cfg.get_effective_workspace()
        if workspace is None:
            return CommandResult(
                handled=True,
                response_text="No active workspace. Use /workspaces to select one.",
            )
        result = _commit_workspace_changes(workspace.path, cmd.message)
        return CommandResult(handled=True, response_text=result)

    return CommandResult(handled=False)


async def handle_callback_query(
    callback_data: str,
    callback_query_id: str,
    daemon_cfg: DaemonConfig,
    bot: BotClient,
    chat_id: int,
    message_id: int,
) -> CommandResult:
    workspace_name = parse_workspace_callback(callback_data)
    if workspace_name is None:
        await bot.answer_callback_query(callback_query_id)
        return CommandResult(handled=False)

    workspace = daemon_cfg.get_workspace(workspace_name)
    if workspace is None:
        daemon_cfg.refresh_workspaces()
        workspace = daemon_cfg.get_workspace(workspace_name)
    if workspace is None:
        await bot.answer_callback_query(
            callback_query_id, text=f"Unknown workspace: {workspace_name}"
        )
        return CommandResult(handled=True)

    daemon_cfg.state.set_active_workspace(workspace_name)
    session = daemon_cfg.state.get_session(workspace_name)

    if session.has_sessions():
        await bot.answer_callback_query(
            callback_query_id, text=f"Switched to {workspace_name} (has sessions)"
        )
    else:
        await bot.answer_callback_query(
            callback_query_id, text=f"Switched to {workspace_name}"
        )

    names = daemon_cfg.workspace_names()
    keyboard = make_workspace_keyboard(names)
    header = f"Current: `{workspace_name}`\n\nSelect a workspace:"
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=header,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    return CommandResult(
        handled=True,
        switch_workspace=workspace_name,
    )


def get_workspace_cwd(daemon_cfg: DaemonConfig) -> Path | None:
    workspace = daemon_cfg.get_effective_workspace()
    if workspace is None:
        return None
    return workspace.path


def apply_workspace_cwd(daemon_cfg: DaemonConfig) -> Path | None:
    cwd = get_workspace_cwd(daemon_cfg)
    if cwd is not None:
        os.chdir(cwd)
    return cwd
