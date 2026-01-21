"""Tests for callback query dispatch to command backends."""

from __future__ import annotations

from takopi.telegram.commands.dispatch import _parse_callback_data


class TestParseCallbackData:
    """Tests for _parse_callback_data function."""

    def test_simple_command(self) -> None:
        """Parse callback data with only command_id."""
        command_id, args_text = _parse_callback_data("ralph")
        assert command_id == "ralph"
        assert args_text == ""

    def test_command_with_single_arg(self) -> None:
        """Parse callback data with command_id and one argument."""
        command_id, args_text = _parse_callback_data("ralph:clarify")
        assert command_id == "ralph"
        assert args_text == "clarify"

    def test_command_with_multiple_args(self) -> None:
        """Parse callback data with command_id and multiple colon-separated args."""
        command_id, args_text = _parse_callback_data("ralph:clarify:123:abc")
        assert command_id == "ralph"
        assert args_text == "clarify:123:abc"

    def test_command_lowercase_normalization(self) -> None:
        """Ensure command_id is lowercased, args_text preserved."""
        command_id, args_text = _parse_callback_data("Ralph:Clarify")
        assert command_id == "ralph"
        assert args_text == "Clarify"

    def test_empty_args_after_colon(self) -> None:
        """Handle callback data with trailing colon (empty args)."""
        command_id, args_text = _parse_callback_data("ralph:")
        assert command_id == "ralph"
        assert args_text == ""

    def test_complex_args_with_special_chars(self) -> None:
        """Parse args containing special characters like = and &."""
        command_id, args_text = _parse_callback_data("mycommand:action=yes&id=42")
        assert command_id == "mycommand"
        assert args_text == "action=yes&id=42"

    def test_command_with_numbers(self) -> None:
        """Parse callback data with numeric command and args."""
        command_id, args_text = _parse_callback_data("cmd123:456")
        assert command_id == "cmd123"
        assert args_text == "456"

    def test_command_with_underscores(self) -> None:
        """Parse callback data with underscores in command and args."""
        command_id, args_text = _parse_callback_data("my_command:my_arg")
        assert command_id == "my_command"
        assert args_text == "my_arg"

    def test_command_with_dashes(self) -> None:
        """Parse callback data with dashes in command and args."""
        command_id, args_text = _parse_callback_data("my-command:my-arg")
        assert command_id == "my-command"
        assert args_text == "my-arg"

    def test_empty_string(self) -> None:
        """Handle empty callback data (edge case)."""
        command_id, args_text = _parse_callback_data("")
        assert command_id == ""
        assert args_text == ""

    def test_only_colon(self) -> None:
        """Handle callback data that is only a colon."""
        command_id, args_text = _parse_callback_data(":")
        assert command_id == ""
        assert args_text == ""

    def test_whitespace_preserved(self) -> None:
        """Whitespace in args should be preserved."""
        command_id, args_text = _parse_callback_data("cmd:arg with spaces")
        assert command_id == "cmd"
        assert args_text == "arg with spaces"

    def test_json_like_args(self) -> None:
        """Parse args that look like JSON (no nested colons in this example)."""
        command_id, args_text = _parse_callback_data('cmd:{"key":"value"}')
        assert command_id == "cmd"
        # First split at : means args_text contains full JSON after first colon
        assert args_text == '{"key":"value"}'

    def test_url_like_args(self) -> None:
        """Parse args containing URL-like patterns with colons."""
        command_id, args_text = _parse_callback_data("cmd:https://example.com")
        assert command_id == "cmd"
        assert args_text == "https://example.com"


class TestParseCallbackDataEdgeCases:
    """Edge case tests for _parse_callback_data."""

    def test_unicode_command(self) -> None:
        """Handle unicode characters in command_id (lowercased)."""
        command_id, args_text = _parse_callback_data("Ümläut:arg")
        assert command_id == "ümläut"
        assert args_text == "arg"

    def test_unicode_args(self) -> None:
        """Handle unicode characters in args (preserved)."""
        command_id, args_text = _parse_callback_data("cmd:日本語")
        assert command_id == "cmd"
        assert args_text == "日本語"

    def test_very_long_args(self) -> None:
        """Handle very long argument strings."""
        long_arg = "x" * 1000
        command_id, args_text = _parse_callback_data(f"cmd:{long_arg}")
        assert command_id == "cmd"
        assert args_text == long_arg

    def test_multiple_colons_in_args(self) -> None:
        """Ensure only first colon is used as delimiter."""
        command_id, args_text = _parse_callback_data("cmd:a:b:c:d")
        assert command_id == "cmd"
        assert args_text == "a:b:c:d"
