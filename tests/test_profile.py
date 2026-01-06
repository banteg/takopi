"""Tests for profile configuration and switching."""

from pathlib import Path

import pytest

from takopi.profile import (
    Profile,
    ProfileConfig,
    ProfileId,
    get_profile_engine_config,
    merge_engine_config,
    parse_profiles,
)
from takopi.config import ConfigError


def test_parse_profiles_empty_config() -> None:
    config: dict = {}
    result = parse_profiles(config, Path("/test/config.toml"))
    assert len(result) == 0
    assert result.profile_names == []


def test_parse_profiles_no_profile_section() -> None:
    config = {"bot_token": "abc", "chat_id": 123}
    result = parse_profiles(config, Path("/test/config.toml"))
    assert len(result) == 0


def test_parse_profiles_single_profile() -> None:
    config = {
        "profile": {
            "coding": {
                "default_engine": "claude",
            }
        }
    }
    result = parse_profiles(config, Path("/test/config.toml"))

    assert len(result) == 1
    assert "coding" in result
    assert result.profile_names == ["coding"]

    profile = result.get("coding")
    assert profile is not None
    assert profile.name == "coding"
    assert profile.default_engine == "claude"
    assert profile.engine_configs == {}


def test_parse_profiles_with_engine_configs() -> None:
    config = {
        "profile": {
            "dev": {
                "default_engine": "claude",
                "claude": {
                    "model": "opus",
                    "allowed_tools": ["Bash", "Read"],
                },
                "codex": {
                    "profile": "fast-mode",
                },
            }
        }
    }
    result = parse_profiles(config, Path("/test/config.toml"))

    profile = result.get("dev")
    assert profile is not None
    assert profile.default_engine == "claude"
    assert "claude" in profile.engine_configs
    assert profile.engine_configs["claude"]["model"] == "opus"
    assert profile.engine_configs["claude"]["allowed_tools"] == ["Bash", "Read"]
    assert profile.engine_configs["codex"]["profile"] == "fast-mode"


def test_parse_profiles_multiple_profiles() -> None:
    config = {
        "profile": {
            "dev": {"default_engine": "claude"},
            "prod": {"default_engine": "codex"},
            "quick": {},
        }
    }
    result = parse_profiles(config, Path("/test/config.toml"))

    assert len(result) == 3
    assert result.profile_names == ["dev", "prod", "quick"]
    assert result.get("dev").default_engine == "claude"
    assert result.get("prod").default_engine == "codex"
    assert result.get("quick").default_engine is None


def test_parse_profiles_invalid_section() -> None:
    config = {"profile": "not a table"}
    with pytest.raises(ConfigError, match="expected a table"):
        parse_profiles(config, Path("/test/config.toml"))


def test_parse_profiles_invalid_profile_data() -> None:
    config = {"profile": {"bad": "not a table"}}
    with pytest.raises(ConfigError, match="expected a table"):
        parse_profiles(config, Path("/test/config.toml"))


def test_parse_profiles_invalid_default_engine() -> None:
    config = {"profile": {"test": {"default_engine": 123}}}
    with pytest.raises(ConfigError, match="expected a non-empty string"):
        parse_profiles(config, Path("/test/config.toml"))


def test_profile_config_contains() -> None:
    profiles = ProfileConfig(
        profiles={"a": Profile(name="a"), "b": Profile(name="b")}
    )
    assert "a" in profiles
    assert "c" not in profiles


def test_profile_config_has_profiles() -> None:
    empty = ProfileConfig()
    assert empty.profile_names == []

    with_profiles = ProfileConfig(profiles={"a": Profile(name="a")})
    assert "a" in with_profiles.profile_names


def test_merge_engine_config_empty_profile() -> None:
    base = {"model": "sonnet", "tools": ["Bash"]}
    result = merge_engine_config(base, None)
    assert result == base


def test_merge_engine_config_override() -> None:
    base = {"model": "sonnet", "tools": ["Bash"]}
    profile = {"model": "opus"}
    result = merge_engine_config(base, profile)

    assert result["model"] == "opus"
    assert result["tools"] == ["Bash"]


def test_merge_engine_config_add_new_key() -> None:
    base = {"model": "sonnet"}
    profile = {"extra_args": ["--verbose"]}
    result = merge_engine_config(base, profile)

    assert result["model"] == "sonnet"
    assert result["extra_args"] == ["--verbose"]


def test_merge_engine_config_nested_dict() -> None:
    base = {"nested": {"a": 1, "b": 2}}
    profile = {"nested": {"b": 3, "c": 4}}
    result = merge_engine_config(base, profile)

    assert result["nested"] == {"a": 1, "b": 3, "c": 4}


def test_get_profile_engine_config_no_profile() -> None:
    config = {"claude": {"model": "sonnet"}}
    result = get_profile_engine_config(config, "claude", None)
    assert result == {"model": "sonnet"}


def test_get_profile_engine_config_with_profile() -> None:
    config = {"claude": {"model": "sonnet", "tools": ["Bash"]}}
    profile = Profile(
        name="dev",
        engine_configs={"claude": {"model": "opus"}},
    )
    result = get_profile_engine_config(config, "claude", profile)

    assert result["model"] == "opus"
    assert result["tools"] == ["Bash"]


def test_get_profile_engine_config_missing_engine() -> None:
    config = {}
    profile = Profile(
        name="dev",
        engine_configs={"claude": {"model": "opus"}},
    )
    result = get_profile_engine_config(config, "claude", profile)

    assert result == {"model": "opus"}


def test_get_profile_engine_config_profile_no_engine_override() -> None:
    config = {"claude": {"model": "sonnet"}}
    profile = Profile(name="dev", engine_configs={})
    result = get_profile_engine_config(config, "claude", profile)

    assert result == {"model": "sonnet"}
