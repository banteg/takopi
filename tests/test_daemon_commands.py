from takopi.commands import (
    DropCommand,
    NewCommand,
    SessionsCommand,
    WorkspaceCommand,
    WorkspacesCommand,
    is_daemon_command,
    parse_daemon_command,
    strip_daemon_command,
)


class TestParseDaemonCommand:
    def test_parse_new(self) -> None:
        cmd = parse_daemon_command("/new")
        assert isinstance(cmd, NewCommand)

    def test_parse_new_with_bot_mention(self) -> None:
        cmd = parse_daemon_command("/new@mybot")
        assert isinstance(cmd, NewCommand)

    def test_parse_new_with_trailing_space(self) -> None:
        cmd = parse_daemon_command("/new ")
        assert isinstance(cmd, NewCommand)

    def test_parse_workspace_with_name(self) -> None:
        cmd = parse_daemon_command("/workspace myproject")
        assert isinstance(cmd, WorkspaceCommand)
        assert cmd.name == "myproject"

    def test_parse_workspace_with_bot_mention(self) -> None:
        cmd = parse_daemon_command("/workspace@mybot myproject")
        assert isinstance(cmd, WorkspaceCommand)
        assert cmd.name == "myproject"

    def test_parse_workspaces(self) -> None:
        cmd = parse_daemon_command("/workspaces")
        assert isinstance(cmd, WorkspacesCommand)

    def test_parse_workspaces_with_bot_mention(self) -> None:
        cmd = parse_daemon_command("/workspaces@mybot")
        assert isinstance(cmd, WorkspacesCommand)

    def test_parse_sessions(self) -> None:
        cmd = parse_daemon_command("/sessions")
        assert isinstance(cmd, SessionsCommand)

    def test_parse_sessions_with_bot_mention(self) -> None:
        cmd = parse_daemon_command("/sessions@mybot")
        assert isinstance(cmd, SessionsCommand)

    def test_parse_drop_with_engine(self) -> None:
        cmd = parse_daemon_command("/drop codex")
        assert isinstance(cmd, DropCommand)
        assert cmd.engine == "codex"

    def test_parse_drop_with_bot_mention(self) -> None:
        cmd = parse_daemon_command("/drop@mybot opencode")
        assert isinstance(cmd, DropCommand)
        assert cmd.engine == "opencode"

    def test_parse_unknown_command(self) -> None:
        cmd = parse_daemon_command("/unknown")
        assert cmd is None

    def test_parse_empty_string(self) -> None:
        cmd = parse_daemon_command("")
        assert cmd is None

    def test_parse_regular_message(self) -> None:
        cmd = parse_daemon_command("hello world")
        assert cmd is None

    def test_parse_workspace_without_name(self) -> None:
        cmd = parse_daemon_command("/workspace")
        assert cmd is None

    def test_parse_drop_without_engine(self) -> None:
        cmd = parse_daemon_command("/drop")
        assert cmd is None

    def test_parse_case_insensitive(self) -> None:
        cmd = parse_daemon_command("/NEW")
        assert isinstance(cmd, NewCommand)

        cmd = parse_daemon_command("/WORKSPACES")
        assert isinstance(cmd, WorkspacesCommand)


class TestIsDaemonCommand:
    def test_is_daemon_command_true(self) -> None:
        assert is_daemon_command("/new")
        assert is_daemon_command("/workspaces")
        assert is_daemon_command("/workspace foo")
        assert is_daemon_command("/sessions")
        assert is_daemon_command("/drop codex")

    def test_is_daemon_command_false(self) -> None:
        assert not is_daemon_command("")
        assert not is_daemon_command("hello")
        assert not is_daemon_command("/unknown")
        assert not is_daemon_command("/cancel")


class TestStripDaemonCommand:
    def test_strip_new_only(self) -> None:
        text, cmd = strip_daemon_command("/new")
        assert text == ""
        assert isinstance(cmd, NewCommand)

    def test_strip_new_with_following_text(self) -> None:
        text, cmd = strip_daemon_command("/new\nhello world")
        assert text == "hello world"
        assert isinstance(cmd, NewCommand)

    def test_strip_workspace_only(self) -> None:
        text, cmd = strip_daemon_command("/workspace myproject")
        assert text == ""
        assert isinstance(cmd, WorkspaceCommand)
        assert cmd.name == "myproject"

    def test_strip_workspace_with_following_text(self) -> None:
        text, cmd = strip_daemon_command("/workspace myproject do something")
        assert text == "do something"
        assert isinstance(cmd, WorkspaceCommand)

    def test_strip_workspaces_with_following_text(self) -> None:
        text, cmd = strip_daemon_command("/workspaces\nshow me the list")
        assert text == "show me the list"
        assert isinstance(cmd, WorkspacesCommand)

    def test_strip_sessions_only(self) -> None:
        text, cmd = strip_daemon_command("/sessions")
        assert text == ""
        assert isinstance(cmd, SessionsCommand)

    def test_strip_drop_only(self) -> None:
        text, cmd = strip_daemon_command("/drop codex")
        assert text == ""
        assert isinstance(cmd, DropCommand)

    def test_strip_drop_with_following_text(self) -> None:
        text, cmd = strip_daemon_command("/drop codex and start fresh")
        assert text == "and start fresh"
        assert isinstance(cmd, DropCommand)

    def test_strip_non_command(self) -> None:
        text, cmd = strip_daemon_command("hello world")
        assert text == "hello world"
        assert cmd is None

    def test_strip_empty(self) -> None:
        text, cmd = strip_daemon_command("")
        assert text == ""
        assert cmd is None

    def test_strip_preserves_leading_whitespace_lines(self) -> None:
        text, cmd = strip_daemon_command("\n\n/new\nhello")
        assert text == "hello"
        assert isinstance(cmd, NewCommand)
