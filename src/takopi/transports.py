from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .backends import EngineBackend, SetupIssue
from .config import ConfigError, ProjectsConfig
from .router import AutoRouter
from .settings import TakopiSettings


@dataclass(frozen=True, slots=True)
class SetupResult:
    issues: list[SetupIssue]
    config_path: Path

    @property
    def ok(self) -> bool:
        return not self.issues


class TransportBackend(Protocol):
    id: str
    description: str

    def check_setup(
        self,
        engine_backend: EngineBackend,
        *,
        transport_override: str | None = None,
    ) -> SetupResult: ...

    def interactive_setup(self, *, force: bool) -> bool: ...

    def lock_token(
        self, *, settings: TakopiSettings, config_path: Path
    ) -> str | None: ...

    def build_and_run(
        self,
        *,
        settings: TakopiSettings,
        config_path: Path,
        router: AutoRouter,
        projects: ProjectsConfig,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None: ...


_registry: dict[str, TransportBackend] = {}
_builtins_loaded = False


def register_transport(backend: TransportBackend) -> None:
    existing = _registry.get(backend.id)
    if existing is not None and existing is not backend:
        raise ConfigError(f"Transport {backend.id!r} is already registered.")
    _registry[backend.id] = backend


def register_builtin_transports() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    from .telegram.backend import telegram_backend

    register_transport(telegram_backend)
    _builtins_loaded = True


def get_transport(transport_id: str) -> TransportBackend:
    register_builtin_transports()
    try:
        return _registry[transport_id]
    except KeyError:
        available = ", ".join(sorted(_registry))
        raise ConfigError(
            f"Unknown transport {transport_id!r}. Available: {available}."
        ) from None


def list_transports() -> list[str]:
    register_builtin_transports()
    return sorted(_registry)
