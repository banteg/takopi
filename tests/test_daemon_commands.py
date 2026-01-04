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


class TestNastyInputs:
    """Test edge cases with unicode, control characters, and malformed input."""

    def test_unicode_non_breaking_space(self) -> None:
        # Non-breaking space U+00A0
        cmd = parse_daemon_command("/new\u00a0")
        assert isinstance(cmd, NewCommand)

    def test_unicode_em_space(self) -> None:
        # Em space U+2003
        cmd = parse_daemon_command("/workspace\u2003myproject")
        assert isinstance(cmd, WorkspaceCommand)
        assert cmd.name == "myproject"

    def test_unicode_zero_width_space(self) -> None:
        # Zero-width space U+200B - should NOT be treated as separator
        cmd = parse_daemon_command("/new\u200b")
        # The ZWSP is not whitespace, so it may be treated as part of a bot mention
        # This tests that the code doesn't crash
        assert cmd is None or isinstance(cmd, NewCommand)

    def test_unicode_rtl_marker(self) -> None:
        # Right-to-left mark U+200F
        cmd = parse_daemon_command("/workspace\u200ftest")
        # RTL marker is not whitespace, so "test" is embedded
        assert cmd is None or isinstance(cmd, WorkspaceCommand)

    def test_unicode_bom(self) -> None:
        # Byte order mark at start
        cmd = parse_daemon_command("\ufeff/new")
        # BOM is not stripped by text.strip()
        assert cmd is None

    def test_null_byte_in_command(self) -> None:
        # Null byte embedded
        cmd = parse_daemon_command("/new\x00extra")
        # Regex may or may not match - just verify no crash
        assert cmd is None or isinstance(cmd, NewCommand)

    def test_control_characters(self) -> None:
        # Various control characters
        for char in ["\x01", "\x02", "\x1b", "\x7f"]:
            cmd = parse_daemon_command(f"/new{char}")
            # Should not crash
            assert cmd is None or isinstance(cmd, NewCommand)

    def test_very_long_workspace_name(self) -> None:
        # Very long workspace name (1000 chars)
        long_name = "a" * 1000
        cmd = parse_daemon_command(f"/workspace {long_name}")
        assert isinstance(cmd, WorkspaceCommand)
        assert cmd.name == long_name

    def test_very_long_input(self) -> None:
        # Very long input string (100KB)
        long_input = "/new\n" + "x" * 100_000
        text, cmd = strip_daemon_command(long_input)
        assert isinstance(cmd, NewCommand)
        assert len(text) == 100_000

    def test_unicode_workspace_name(self) -> None:
        # Emoji and unicode in workspace name
        cmd = parse_daemon_command("/workspace 日本語プロジェクト")
        assert isinstance(cmd, WorkspaceCommand)
        assert cmd.name == "日本語プロジェクト"

    def test_emoji_workspace_name(self) -> None:
        cmd = parse_daemon_command("/workspace 🚀project")
        assert isinstance(cmd, WorkspaceCommand)
        assert cmd.name == "🚀project"

    def test_newline_variations(self) -> None:
        # CR, LF, CRLF
        for sep in ["\n", "\r", "\r\n"]:
            text, cmd = strip_daemon_command(f"/new{sep}hello")
            assert isinstance(cmd, NewCommand)

    def test_mixed_whitespace(self) -> None:
        # Tab, space, non-breaking space mix
        cmd = parse_daemon_command("/workspace \t \u00a0 myproject")
        # The regex expects \s+ between command and arg
        assert cmd is None or isinstance(cmd, WorkspaceCommand)

    def test_only_whitespace(self) -> None:
        cmd = parse_daemon_command("   \t\n   ")
        assert cmd is None

    def test_command_with_only_bot_mention(self) -> None:
        # Bot mention but no actual command
        cmd = parse_daemon_command("@mybot")
        assert cmd is None

    def test_workspace_name_with_slash(self) -> None:
        # Workspace name containing slash
        cmd = parse_daemon_command("/workspace path/to/project")
        assert isinstance(cmd, WorkspaceCommand)
        # Should only take first word
        assert cmd.name == "path/to/project"

    def test_drop_with_unicode_engine(self) -> None:
        cmd = parse_daemon_command("/drop 日本語")
        assert isinstance(cmd, DropCommand)
        assert cmd.engine == "日本語"
