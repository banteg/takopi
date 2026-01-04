import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from takopi.commands import (
    DropCommand,
    NewCommand,
    SessionsCommand,
    WorkspaceCommand,
    WorkspacesCommand,
)
from takopi.daemon import (
    CommandResult,
    DaemonConfig,
    DaemonState,
    WorkspaceSession,
    get_workspace_cwd,
    handle_callback_query,
    handle_daemon_command,
)
from takopi.model import ResumeToken, Workspace


class TestWorkspaceSession:
    def test_empty_session(self) -> None:
        session = WorkspaceSession()
        assert not session.has_sessions()
        assert session.session_count() == 0
        assert session.active_engine is None

    def test_set_resume_token(self) -> None:
        session = WorkspaceSession()
        token = ResumeToken(engine="codex", value="abc123")
        session.set_resume_token("codex", token)

        assert session.has_sessions()
        assert session.session_count() == 1
        assert session.active_engine == "codex"
        assert session.get_resume_token("codex") == token

    def test_set_multiple_tokens(self) -> None:
        session = WorkspaceSession()
        token1 = ResumeToken(engine="codex", value="abc123")
        token2 = ResumeToken(engine="claude", value="def456")

        session.set_resume_token("codex", token1)
        session.set_resume_token("claude", token2)

        assert session.session_count() == 2
        assert session.active_engine == "claude"
        assert session.get_resume_token("codex") == token1
        assert session.get_resume_token("claude") == token2

    def test_clear_engine(self) -> None:
        session = WorkspaceSession()
        token = ResumeToken(engine="codex", value="abc123")
        session.set_resume_token("codex", token)

        session.clear_engine("codex")

        assert not session.has_sessions()
        assert session.active_engine is None
        assert session.get_resume_token("codex") is None

    def test_clear_non_active_engine(self) -> None:
        session = WorkspaceSession()
        token1 = ResumeToken(engine="codex", value="abc123")
        token2 = ResumeToken(engine="claude", value="def456")
        session.set_resume_token("codex", token1)
        session.set_resume_token("claude", token2)

        session.clear_engine("codex")

        assert session.session_count() == 1
        assert session.active_engine == "claude"

    def test_clear_all(self) -> None:
        session = WorkspaceSession()
        session.set_resume_token("codex", ResumeToken(engine="codex", value="abc"))
        session.set_resume_token("claude", ResumeToken(engine="claude", value="def"))

        session.clear_all()

        assert not session.has_sessions()
        assert session.active_engine is None

    def test_to_dict(self) -> None:
        session = WorkspaceSession()
        token = ResumeToken(engine="codex", value="abc123")
        session.set_resume_token("codex", token)

        data = session.to_dict()

        assert "engine_sessions" in data
        assert "codex" in data["engine_sessions"]
        assert data["active_engine"] == "codex"

    def test_from_dict(self) -> None:
        data = {
            "engine_sessions": {"codex": {"engine": "codex", "value": "abc123"}},
            "active_engine": "codex",
        }

        session = WorkspaceSession.from_dict(data)

        assert session.session_count() == 1
        assert session.active_engine == "codex"
        token = session.get_resume_token("codex")
        assert token is not None
        assert token.value == "abc123"

    def test_from_dict_empty(self) -> None:
        session = WorkspaceSession.from_dict({})
        assert not session.has_sessions()


class TestDaemonState:
    def test_empty_state(self) -> None:
        state = DaemonState()
        assert state.active_workspace is None
        assert not state.workspace_sessions

    def test_set_active_workspace(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = DaemonState(_path=state_file)

        state.set_active_workspace("myproject")

        assert state.active_workspace == "myproject"
        assert state_file.exists()

    def test_get_session_creates_new(self) -> None:
        state = DaemonState()
        session = state.get_session("myproject")

        assert isinstance(session, WorkspaceSession)
        assert "myproject" in state.workspace_sessions

    def test_get_session_returns_existing(self) -> None:
        state = DaemonState()
        session1 = state.get_session("myproject")
        session1.set_resume_token("codex", ResumeToken(engine="codex", value="abc"))

        session2 = state.get_session("myproject")

        assert session2 is session1
        assert session2.has_sessions()

    def test_update_session(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = DaemonState(_path=state_file)
        token = ResumeToken(engine="codex", value="abc123")

        state.update_session("myproject", "codex", token)

        session = state.get_session("myproject")
        assert session.get_resume_token("codex") == token

    def test_get_engine_session(self) -> None:
        state = DaemonState()
        token = ResumeToken(engine="codex", value="abc123")
        state.update_session("myproject", "codex", token)

        result = state.get_engine_session("myproject", "codex")

        assert result == token

    def test_clear_engine_session(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = DaemonState(_path=state_file)
        token = ResumeToken(engine="codex", value="abc123")
        state.update_session("myproject", "codex", token)

        state.clear_engine_session("myproject", "codex")

        assert state.get_engine_session("myproject", "codex") is None

    def test_clear_all_sessions(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = DaemonState(_path=state_file)
        state.update_session(
            "myproject", "codex", ResumeToken(engine="codex", value="a")
        )
        state.update_session(
            "myproject", "claude", ResumeToken(engine="claude", value="b")
        )

        state.clear_all_sessions("myproject")

        session = state.get_session("myproject")
        assert not session.has_sessions()

    def test_to_dict(self) -> None:
        state = DaemonState(active_workspace="myproject")
        state.update_session(
            "myproject", "codex", ResumeToken(engine="codex", value="abc")
        )

        data = state.to_dict()

        assert data["active_workspace"] == "myproject"
        assert "myproject" in data["workspace_sessions"]

    def test_from_dict(self) -> None:
        data = {
            "active_workspace": "myproject",
            "workspace_sessions": {
                "myproject": {
                    "engine_sessions": {"codex": {"engine": "codex", "value": "abc"}},
                    "active_engine": "codex",
                }
            },
        }

        state = DaemonState.from_dict(data)

        assert state.active_workspace == "myproject"
        session = state.get_session("myproject")
        assert session.active_engine == "codex"

    def test_load_creates_new_if_missing(self, tmp_path: Path) -> None:
        state_file = tmp_path / "nonexistent.json"

        state = DaemonState.load(state_file)

        assert state.active_workspace is None
        assert not state.workspace_sessions

    def test_load_existing_file(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        data = {
            "active_workspace": "myproject",
            "workspace_sessions": {},
        }
        state_file.write_text(json.dumps(data))

        state = DaemonState.load(state_file)

        assert state.active_workspace == "myproject"

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = DaemonState(_path=state_file)
        state.set_active_workspace("myproject")
        state.update_session(
            "myproject", "codex", ResumeToken(engine="codex", value="abc")
        )

        loaded = DaemonState.load(state_file)

        assert loaded.active_workspace == "myproject"
        token = loaded.get_engine_session("myproject", "codex")
        assert token is not None
        assert token.value == "abc"


class TestDaemonConfig:
    def test_workspace_names(self) -> None:
        workspaces = {
            "project1": Workspace(name="project1", path=Path("/tmp/p1")),
            "project2": Workspace(name="project2", path=Path("/tmp/p2")),
        }
        cfg = DaemonConfig(workspaces=workspaces, state=DaemonState())

        names = cfg.workspace_names()

        assert set(names) == {"project1", "project2"}

    def test_get_workspace(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        cfg = DaemonConfig(workspaces={"project1": ws}, state=DaemonState())

        result = cfg.get_workspace("project1")

        assert result == ws

    def test_get_workspace_not_found(self) -> None:
        cfg = DaemonConfig(workspaces={}, state=DaemonState())

        result = cfg.get_workspace("nonexistent")

        assert result is None

    def test_get_active_workspace(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState(active_workspace="project1")
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)

        result = cfg.get_active_workspace()

        assert result == ws

    def test_get_active_workspace_none_set(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState()
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)

        result = cfg.get_active_workspace()

        assert result is None

    def test_get_effective_workspace_returns_active(self) -> None:
        ws1 = Workspace(name="project1", path=Path("/tmp/p1"))
        ws2 = Workspace(name="project2", path=Path("/tmp/p2"))
        state = DaemonState(active_workspace="project2")
        cfg = DaemonConfig(
            workspaces={"project1": ws1, "project2": ws2},
            state=state,
            default_workspace="project1",
        )

        result = cfg.get_effective_workspace()

        assert result == ws2

    def test_get_effective_workspace_returns_default(self) -> None:
        ws1 = Workspace(name="project1", path=Path("/tmp/p1"))
        ws2 = Workspace(name="project2", path=Path("/tmp/p2"))
        state = DaemonState()
        cfg = DaemonConfig(
            workspaces={"project1": ws1, "project2": ws2},
            state=state,
            default_workspace="project1",
        )

        result = cfg.get_effective_workspace()

        assert result == ws1

    def test_get_effective_workspace_returns_single(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState()
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)

        result = cfg.get_effective_workspace()

        assert result == ws

    def test_get_effective_workspace_returns_none_when_ambiguous(self) -> None:
        ws1 = Workspace(name="project1", path=Path("/tmp/p1"))
        ws2 = Workspace(name="project2", path=Path("/tmp/p2"))
        state = DaemonState()
        cfg = DaemonConfig(
            workspaces={"project1": ws1, "project2": ws2},
            state=state,
        )

        result = cfg.get_effective_workspace()

        assert result is None


@pytest.mark.anyio
class TestHandleDaemonCommand:
    async def test_new_command_clears_sessions(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState(active_workspace="project1")
        state.update_session(
            "project1", "codex", ResumeToken(engine="codex", value="abc")
        )
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            NewCommand(), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.clear_session
        assert result.response_text is not None
        assert "new session" in result.response_text.lower()
        session = state.get_session("project1")
        assert not session.has_sessions()

    async def test_new_command_no_workspace(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            NewCommand(), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "no active workspace" in result.response_text.lower()

    async def test_workspace_command_switches(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState()
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            WorkspaceCommand(name="project1"), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.switch_workspace == "project1"
        assert state.active_workspace == "project1"

    async def test_workspace_command_unknown(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            WorkspaceCommand(name="nonexistent"), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "unknown workspace" in result.response_text.lower()

    async def test_workspaces_command_lists(self) -> None:
        ws1 = Workspace(name="project1", path=Path("/tmp/p1"))
        ws2 = Workspace(name="project2", path=Path("/tmp/p2"))
        state = DaemonState()
        cfg = DaemonConfig(workspaces={"project1": ws1, "project2": ws2}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            WorkspacesCommand(), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.keyboard is not None
        assert "inline_keyboard" in result.keyboard

    async def test_workspaces_command_empty(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)
        cfg.refresh_workspaces = lambda: None
        bot = AsyncMock()

        result = await handle_daemon_command(
            WorkspacesCommand(), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "no workspaces" in result.response_text.lower()

    async def test_sessions_command_shows_sessions(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState(active_workspace="project1")
        state.update_session(
            "project1", "codex", ResumeToken(engine="codex", value="abc123def456")
        )
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            SessionsCommand(), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "codex" in result.response_text
        assert "abc123def456"[:20] in result.response_text

    async def test_sessions_command_no_sessions(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState(active_workspace="project1")
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            SessionsCommand(), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "no active sessions" in result.response_text.lower()

    async def test_sessions_command_no_workspace(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            SessionsCommand(), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "no active workspace" in result.response_text.lower()

    async def test_drop_command_clears_engine(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState(active_workspace="project1")
        state.update_session(
            "project1", "codex", ResumeToken(engine="codex", value="abc")
        )
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            DropCommand(engine="codex"), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "dropped" in result.response_text.lower()
        session = state.get_session("project1")
        assert session.get_resume_token("codex") is None

    async def test_drop_command_no_session(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState(active_workspace="project1")
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            DropCommand(engine="codex"), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "no `codex` session" in result.response_text.lower()

    async def test_drop_command_no_workspace(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)
        bot = AsyncMock()

        result = await handle_daemon_command(
            DropCommand(engine="codex"), cfg, bot, chat_id=123, message_id=1
        )

        assert result.handled
        assert result.response_text is not None
        assert "no active workspace" in result.response_text.lower()


@pytest.mark.anyio
class TestHandleCallbackQuery:
    async def test_invalid_callback_data(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)
        bot = AsyncMock()

        result = await handle_callback_query(
            callback_data="invalid:data",
            callback_query_id="query123",
            daemon_cfg=cfg,
            bot=bot,
            chat_id=123,
            message_id=1,
        )

        assert not result.handled
        bot.answer_callback_query.assert_awaited_once_with("query123")

    async def test_unknown_workspace(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)
        cfg.refresh_workspaces = lambda: None
        bot = AsyncMock()

        result = await handle_callback_query(
            callback_data="ws:nonexistent",
            callback_query_id="query123",
            daemon_cfg=cfg,
            bot=bot,
            chat_id=123,
            message_id=1,
        )

        assert result.handled
        bot.answer_callback_query.assert_awaited_once()
        call_args = bot.answer_callback_query.call_args
        assert "Unknown workspace" in call_args.kwargs.get("text", "")

    async def test_valid_workspace_no_sessions(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState()
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_callback_query(
            callback_data="ws:project1",
            callback_query_id="query123",
            daemon_cfg=cfg,
            bot=bot,
            chat_id=123,
            message_id=1,
        )

        assert result.handled
        assert result.switch_workspace == "project1"
        assert state.active_workspace == "project1"
        bot.edit_message_text.assert_awaited_once()

    async def test_valid_workspace_with_sessions(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState()
        state.update_session(
            "project1", "codex", ResumeToken(engine="codex", value="abc")
        )
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)
        bot = AsyncMock()

        result = await handle_callback_query(
            callback_data="ws:project1",
            callback_query_id="query123",
            daemon_cfg=cfg,
            bot=bot,
            chat_id=123,
            message_id=1,
        )

        assert result.handled
        call_args = bot.answer_callback_query.call_args
        assert "has sessions" in call_args.kwargs.get("text", "")


class TestGetWorkspaceCwd:
    def test_with_active_workspace(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState(active_workspace="project1")
        cfg = DaemonConfig(workspaces={"project1": ws}, state=state)

        result = get_workspace_cwd(cfg)

        assert result == Path("/tmp/p1")

    def test_with_default_workspace(self) -> None:
        ws = Workspace(name="project1", path=Path("/tmp/p1"))
        state = DaemonState()
        cfg = DaemonConfig(
            workspaces={"project1": ws}, state=state, default_workspace="project1"
        )

        result = get_workspace_cwd(cfg)

        assert result == Path("/tmp/p1")

    def test_no_workspace(self) -> None:
        state = DaemonState()
        cfg = DaemonConfig(workspaces={}, state=state)

        result = get_workspace_cwd(cfg)

        assert result is None
