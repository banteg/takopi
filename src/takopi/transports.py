from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from .backends import EngineBackend, SetupIssue
from .config import ConfigError, ProjectsConfig
from .plugins import (
    PluginLoadFailed,
    PluginNotFound,
    TRANSPORT_GROUP,
    load_entrypoint,
    list_ids,
)
from .router import AutoRouter
from .settings import TakopiSettings


@dataclass(frozen=True, slots=True)
class SetupResult:
    issues: list[SetupIssue]
    config_path: Path

    @property
    def ok(self) -> bool:
        return not self.issues


@runtime_checkable
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


def _validate_transport_backend(backend: object, ep) -> None:
    if not isinstance(backend, TransportBackend):
        raise TypeError(f"{ep.value} is not a TransportBackend")
    if backend.id != ep.name:
        raise ValueError(
            f"{ep.value} transport id {backend.id!r} does not match entrypoint {ep.name!r}"
        )


def get_transport(
    transport_id: str, *, allowlist: Iterable[str] | None = None
) -> TransportBackend:
    try:
        backend = load_entrypoint(
            TRANSPORT_GROUP,
            transport_id,
            allowlist=allowlist,
            validator=_validate_transport_backend,
        )
    except PluginNotFound as exc:
        available = ", ".join(exc.available)
        raise ConfigError(
            f"Unknown transport {transport_id!r}. Available: {available}."
        ) from exc
    except PluginLoadFailed as exc:
        raise ConfigError(f"Failed to load transport {transport_id!r}: {exc}") from exc
    return backend


def list_transports(*, allowlist: Iterable[str] | None = None) -> list[str]:
    return list_ids(TRANSPORT_GROUP, allowlist=allowlist)
