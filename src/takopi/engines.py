from __future__ import annotations

from collections.abc import Iterable

from .backends import EngineBackend
from .config import ConfigError
from .plugins import ENGINE_GROUP, list_ids, load_plugin_backend
from .ids import RESERVED_ENGINE_IDS


def _validate_engine_backend(backend: object, ep) -> None:
    if not isinstance(backend, EngineBackend):
        raise TypeError(f"{ep.value} is not an EngineBackend")
    if backend.id != ep.name:
        raise ValueError(
            f"{ep.value} engine id {backend.id!r} does not match entrypoint {ep.name!r}"
        )


def get_backend(
    engine_id: str, *, allowlist: Iterable[str] | None = None
) -> EngineBackend:
    if engine_id.lower() in RESERVED_ENGINE_IDS:
        raise ConfigError(f"Engine id {engine_id!r} is reserved.")
    backend = load_plugin_backend(
        ENGINE_GROUP,
        engine_id,
        allowlist=allowlist,
        validator=_validate_engine_backend,
        kind_label="engine",
    )
    assert backend is not None
    return backend


def list_backends(*, allowlist: Iterable[str] | None = None) -> list[EngineBackend]:
    backends: list[EngineBackend] = []
    for engine_id in list_backend_ids(allowlist=allowlist):
        try:
            backends.append(get_backend(engine_id, allowlist=allowlist))
        except ConfigError:
            continue
    if not backends:
        raise ConfigError("No engine backends are available.")
    return backends


def list_backend_ids(*, allowlist: Iterable[str] | None = None) -> list[str]:
    return list_ids(
        ENGINE_GROUP,
        allowlist=allowlist,
        reserved_ids=RESERVED_ENGINE_IDS,
    )
