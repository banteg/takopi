from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from collections.abc import Iterable

from .backends import EngineBackend, SetupIssue
from .plugins import TRANSPORT_GROUP, list_ids, load_plugin_backend
from .transport_runtime import TransportRuntime


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

    async def interactive_setup(self, *, force: bool) -> bool: ...

    def lock_token(
        self, *, transport_config: object, _config_path: Path
    ) -> str | None: ...

    def build_and_run(
        self,
        *,
        transport_config: object,
        config_path: Path,
        runtime: TransportRuntime,
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
    backend = load_plugin_backend(
        TRANSPORT_GROUP,
        transport_id,
        allowlist=allowlist,
        validator=_validate_transport_backend,
        kind_label="transport",
    )
    assert backend is not None
    return backend


def list_transports(*, allowlist: Iterable[str] | None = None) -> list[str]:
    return list_ids(TRANSPORT_GROUP, allowlist=allowlist)
