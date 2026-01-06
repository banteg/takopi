"""Factory for building routers with profile support."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backends import EngineBackend
from .config import ConfigError
from .engines import get_engine_config, list_backends
from .logging import get_logger
from .model import EngineId
from .profile import Profile, ProfileConfig, get_profile_engine_config, parse_profiles
from .router import AutoRouter, RunnerEntry

logger = get_logger(__name__)


@dataclass
class RouterFactory:
    """Factory for building routers with profile support.

    This class encapsulates the logic for building an AutoRouter,
    allowing routers to be rebuilt when the active profile changes.
    """

    config: dict[str, Any]
    config_path: Path
    backends: list[EngineBackend]
    profile_config: ProfileConfig
    base_default_engine: EngineId

    @classmethod
    def create(
        cls,
        *,
        config: dict[str, Any],
        config_path: Path,
        default_engine_override: EngineId | None = None,
    ) -> RouterFactory:
        """Create a RouterFactory from config.

        Args:
            config: Full config dict
            config_path: Path to config file
            default_engine_override: CLI override for default engine

        Returns:
            RouterFactory instance
        """
        backends = list_backends()
        profile_config = parse_profiles(config, config_path)

        # Resolve base default engine (without profile)
        base_default_engine = _resolve_default_engine(
            override=default_engine_override,
            config=config,
            config_path=config_path,
            backends=backends,
        )

        return cls(
            config=config,
            config_path=config_path,
            backends=backends,
            profile_config=profile_config,
            base_default_engine=base_default_engine,
        )

    @property
    def profile_names(self) -> list[str]:
        """Return list of available profile names."""
        return self.profile_config.profile_names

    @property
    def has_profiles(self) -> bool:
        """Return True if any profiles are defined."""
        return len(self.profile_config) > 0

    def get_profile(self, name: str | None) -> Profile | None:
        """Get a profile by name, or None for base config."""
        if name is None:
            return None
        return self.profile_config.get(name)

    def build_router(self, profile_name: str | None = None) -> AutoRouter:
        """Build a router with the specified profile applied.

        Args:
            profile_name: Name of profile to apply, or None for base config

        Returns:
            AutoRouter configured with the profile's settings
        """
        profile = self.get_profile(profile_name)

        # Determine default engine for this profile
        if profile is not None and profile.default_engine is not None:
            default_engine = profile.default_engine
            # Validate the profile's default engine exists
            backend_ids = {backend.id for backend in self.backends}
            if default_engine not in backend_ids:
                available = ", ".join(sorted(backend_ids))
                raise ConfigError(
                    f"Profile {profile_name!r} specifies unknown default engine "
                    f"{default_engine!r}. Available: {available}."
                )
        else:
            default_engine = self.base_default_engine

        entries: list[RunnerEntry] = []
        warnings: list[str] = []

        for backend in self.backends:
            engine_id = backend.id
            issue: str | None = None
            engine_cfg: dict

            try:
                # Get engine config with profile overrides applied
                engine_cfg = get_profile_engine_config(
                    self.config,
                    engine_id,
                    profile,
                )
            except ConfigError as exc:
                if engine_id == default_engine:
                    raise
                issue = str(exc)
                engine_cfg = {}

            try:
                runner = backend.build_runner(engine_cfg, self.config_path)
            except Exception as exc:
                if engine_id == default_engine:
                    raise
                issue = issue or str(exc)
                if engine_cfg:
                    try:
                        runner = backend.build_runner({}, self.config_path)
                    except Exception as fallback_exc:
                        warnings.append(f"{engine_id}: {issue or str(fallback_exc)}")
                        continue
                else:
                    warnings.append(f"{engine_id}: {issue}")
                    continue

            cmd = backend.cli_cmd or backend.id
            if shutil.which(cmd) is None:
                issue = issue or f"{cmd} not found on PATH"

            if issue and engine_id == default_engine:
                raise ConfigError(
                    f"Default engine {engine_id!r} unavailable: {issue}"
                )

            available = issue is None
            if issue and engine_id != default_engine:
                warnings.append(f"{engine_id}: {issue}")

            entries.append(
                RunnerEntry(
                    engine=engine_id,
                    runner=runner,
                    available=available,
                    issue=issue,
                )
            )

        for warning in warnings:
            logger.warning("setup.warning", issue=warning)

        return AutoRouter(entries=entries, default_engine=default_engine)


def _resolve_default_engine(
    *,
    override: str | None,
    config: dict,
    config_path: Path,
    backends: list[EngineBackend],
) -> str:
    """Resolve the default engine from config and overrides."""
    default_engine = override or config.get("default_engine") or "codex"
    if not isinstance(default_engine, str) or not default_engine.strip():
        raise ConfigError(
            f"Invalid `default_engine` in {config_path}; expected a non-empty string."
        )
    default_engine = default_engine.strip()
    backend_ids = {backend.id for backend in backends}
    if default_engine not in backend_ids:
        available = ", ".join(sorted(backend_ids))
        raise ConfigError(
            f"Unknown default engine {default_engine!r}. Available: {available}."
        )
    return default_engine
