import os
from unittest import mock

from takopi.cli import _is_interactive_terminal as cli_is_interactive
from takopi.logging import _is_terminal


def test_logging_is_terminal_tty_true() -> None:
    with mock.patch("sys.stdout.isatty", return_value=True):
        assert _is_terminal() is True


def test_logging_is_terminal_tty_false() -> None:
    with mock.patch("sys.stdout.isatty", return_value=False):
        assert _is_terminal() is False


def test_logging_is_terminal_in_tmux() -> None:
    env = {"TMUX": "/tmp/tmux-1000/default,1234,0"}
    with (
        mock.patch.dict(os.environ, env, clear=False),
        mock.patch("sys.stdout.isatty", return_value=False),
    ):
        assert _is_terminal() is True


def test_logging_is_terminal_tmux_with_tty() -> None:
    env = {"TMUX": "/tmp/tmux-1000/default,1234,0"}
    with (
        mock.patch.dict(os.environ, env, clear=False),
        mock.patch("sys.stdout.isatty", return_value=True),
    ):
        assert _is_terminal() is True


def test_cli_interactive_tty_true() -> None:
    with (
        mock.patch("sys.stdin.isatty", return_value=True),
        mock.patch("sys.stdout.isatty", return_value=True),
    ):
        assert cli_is_interactive() is True


def test_cli_interactive_tty_false() -> None:
    with (
        mock.patch("sys.stdin.isatty", return_value=False),
        mock.patch("sys.stdout.isatty", return_value=False),
    ):
        assert cli_is_interactive() is False


def test_cli_interactive_in_tmux() -> None:
    env = {"TMUX": "/tmp/tmux-1000/default,1234,0"}
    with (
        mock.patch.dict(os.environ, env, clear=False),
        mock.patch("sys.stdin.isatty", return_value=False),
        mock.patch("sys.stdout.isatty", return_value=False),
    ):
        assert cli_is_interactive() is True


def test_cli_interactive_env_disabled() -> None:
    env = {"TAKOPI_NO_INTERACTIVE": "1"}
    with (
        mock.patch.dict(os.environ, env, clear=False),
        mock.patch("sys.stdin.isatty", return_value=True),
        mock.patch("sys.stdout.isatty", return_value=True),
    ):
        assert cli_is_interactive() is False
