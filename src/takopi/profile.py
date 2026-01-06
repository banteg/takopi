"""Profile configuration for takopi.

Profiles allow users to define named configurations that override
default engine settings. Each profile can specify:
- A default engine to use for new sessions
- Per-engine configuration overrides

Example config:

    [profile.coding]
    default_engine = "claude"

    [profile.coding.claude]
    model = "opus"
    allowed_tools = ["Bash", "Read", "Edit", "Write", "WebSearch"]

    [profile.quick]
    default_engine = "codex"

    [profile.quick.codex]
    profile = "fast-mode"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

from .backends import EngineConfig
from .config import ConfigError
from .model import EngineId

ProfileId: TypeAlias = str


@dataclass(frozen=True, slots=True)
class Profile:
    """A named profile with engine configuration overrides."""

    name: ProfileId
    default_engine: EngineId | None = None
    engine_configs: dict[EngineId, EngineConfig] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    """Container for all parsed profiles."""

    profiles: dict[ProfileId, Profile] = field(default_factory=dict)

    @property
    def profile_names(self) -> list[ProfileId]:
        """Return sorted list of profile names."""
        return sorted(self.profiles.keys())

    def get(self, name: ProfileId) -> Profile | None:
        """Get a profile by name."""
        return self.profiles.get(name)

    def __contains__(self, name: ProfileId) -> bool:
        return name in self.profiles

    def __len__(self) -> int:
        return len(self.profiles)


def parse_profiles(config: dict[str, Any], config_path: Path) -> ProfileConfig:
    """Parse profile sections from config dict.

    Looks for [profile.<name>] sections and extracts:
    - default_engine from profile root
    - engine configs from [profile.<name>.<engine>] subsections
    """
    profile_section = config.get("profile")
    if profile_section is None:
        return ProfileConfig()

    if not isinstance(profile_section, dict):
        raise ConfigError(
            f"Invalid `profile` section in {config_path}; expected a table."
        )

    profiles: dict[ProfileId, Profile] = {}

    for profile_name, profile_data in profile_section.items():
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ConfigError(
                f"Invalid profile name in {config_path}; expected a non-empty string."
            )

        if not isinstance(profile_data, dict):
            raise ConfigError(
                f"Invalid `profile.{profile_name}` in {config_path}; expected a table."
            )

        # Extract default_engine if specified
        default_engine: EngineId | None = None
        default_engine_value = profile_data.get("default_engine")
        if default_engine_value is not None:
            if not isinstance(default_engine_value, str) or not default_engine_value.strip():
                raise ConfigError(
                    f"Invalid `profile.{profile_name}.default_engine` in {config_path}; "
                    "expected a non-empty string."
                )
            default_engine = default_engine_value.strip()

        # Extract per-engine configs (nested tables)
        engine_configs: dict[EngineId, EngineConfig] = {}
        for key, value in profile_data.items():
            if key == "default_engine":
                continue
            if isinstance(value, dict):
                # This is an engine config section
                engine_configs[key] = value

        profiles[profile_name] = Profile(
            name=profile_name,
            default_engine=default_engine,
            engine_configs=engine_configs,
        )

    return ProfileConfig(profiles=profiles)


def merge_engine_config(
    base_config: EngineConfig,
    profile_config: EngineConfig | None,
) -> EngineConfig:
    """Merge profile engine config on top of base config.

    Profile settings override base settings. Nested dicts are merged recursively.
    """
    if profile_config is None:
        return base_config

    result = dict(base_config)
    for key, value in profile_config.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            # Merge nested dicts
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def get_profile_engine_config(
    config: dict[str, Any],
    engine_id: EngineId,
    profile: Profile | None,
) -> EngineConfig:
    """Get engine config with profile overrides applied.

    Args:
        config: Full config dict
        engine_id: Engine to get config for
        profile: Optional profile to apply overrides from

    Returns:
        Engine config with profile overrides merged in
    """
    base_config = config.get(engine_id) or {}
    if not isinstance(base_config, dict):
        base_config = {}

    if profile is None:
        return base_config

    profile_engine_config = profile.engine_configs.get(engine_id)
    return merge_engine_config(base_config, profile_engine_config)
