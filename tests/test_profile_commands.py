"""Tests for profile and default engine command parsing in the bridge."""

from takopi.telegram.bridge import (
    _is_profiles_command,
    _parse_default_command,
    _parse_profile_command,
)


def test_parse_profile_command_with_name() -> None:
    is_cmd, name = _parse_profile_command("/profile coding")
    assert is_cmd is True
    assert name == "coding"


def test_parse_profile_command_no_arg() -> None:
    is_cmd, name = _parse_profile_command("/profile")
    assert is_cmd is True
    assert name is None


def test_parse_profile_command_not_profile() -> None:
    is_cmd, name = _parse_profile_command("/claude hello")
    assert is_cmd is False
    assert name is None


def test_parse_profile_command_with_bot_suffix() -> None:
    is_cmd, name = _parse_profile_command("/profile@mybot dev")
    assert is_cmd is True
    assert name == "dev"


def test_parse_profile_command_empty() -> None:
    is_cmd, name = _parse_profile_command("")
    assert is_cmd is False
    assert name is None


def test_parse_profile_command_whitespace_name() -> None:
    is_cmd, name = _parse_profile_command("/profile   ")
    assert is_cmd is True
    assert name is None


def test_parse_profile_command_case_insensitive() -> None:
    is_cmd, name = _parse_profile_command("/Profile Dev")
    assert is_cmd is True
    assert name == "Dev"


def test_parse_profile_command_uppercase() -> None:
    is_cmd, name = _parse_profile_command("/PROFILE LOUD")
    assert is_cmd is True
    assert name == "LOUD"


def test_is_profiles_command_basic() -> None:
    assert _is_profiles_command("/profiles") is True


def test_is_profiles_command_with_suffix() -> None:
    assert _is_profiles_command("/profiles@bot") is True


def test_is_profiles_command_case_insensitive() -> None:
    assert _is_profiles_command("/Profiles") is True
    assert _is_profiles_command("/PROFILES") is True


def test_is_profiles_command_not_profiles() -> None:
    assert _is_profiles_command("/profile") is False
    assert _is_profiles_command("/cancel") is False
    assert _is_profiles_command("profiles") is False


def test_is_profiles_command_empty() -> None:
    assert _is_profiles_command("") is False


def test_is_profiles_command_with_extra_text() -> None:
    # /profiles ignores extra text (just like /cancel)
    assert _is_profiles_command("/profiles list") is True


# Tests for /default command


def test_parse_default_command_with_engine() -> None:
    is_cmd, engine = _parse_default_command("/default claude")
    assert is_cmd is True
    assert engine == "claude"


def test_parse_default_command_no_arg() -> None:
    is_cmd, engine = _parse_default_command("/default")
    assert is_cmd is True
    assert engine is None


def test_parse_default_command_not_default() -> None:
    is_cmd, engine = _parse_default_command("/claude hello")
    assert is_cmd is False
    assert engine is None


def test_parse_default_command_with_bot_suffix() -> None:
    is_cmd, engine = _parse_default_command("/default@mybot codex")
    assert is_cmd is True
    assert engine == "codex"


def test_parse_default_command_empty() -> None:
    is_cmd, engine = _parse_default_command("")
    assert is_cmd is False
    assert engine is None


def test_parse_default_command_whitespace_engine() -> None:
    is_cmd, engine = _parse_default_command("/default   ")
    assert is_cmd is True
    assert engine is None


def test_parse_default_command_case_insensitive() -> None:
    is_cmd, engine = _parse_default_command("/Default Claude")
    assert is_cmd is True
    assert engine == "Claude"


def test_parse_default_command_uppercase() -> None:
    is_cmd, engine = _parse_default_command("/DEFAULT CODEX")
    assert is_cmd is True
    assert engine == "CODEX"
