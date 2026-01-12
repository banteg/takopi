from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
import re
from typing import Any
from collections.abc import Callable

from .ids import ID_PATTERN, is_valid_id

ENGINE_GROUP = "takopi.engine_backends"
TRANSPORT_GROUP = "takopi.transport_backends"
COMMAND_GROUP = "takopi.command_backends"

_CANONICAL_NAME_RE = re.compile(r"[-_.]+")


@dataclass(frozen=True, slots=True)
class PluginLoadError:
    group: str
    name: str
    value: str
    distribution: str | None
    error: str


class PluginLoadFailed(RuntimeError):
    def __init__(self, error: PluginLoadError) -> None:
        super().__init__(error.error)
        self.error = error


class PluginNotFound(LookupError):
    def __init__(self, group: str, name: str, available: Iterable[str]) -> None:
        self.group = group
        self.name = name
        self.available = tuple(sorted(available))
        message = f"{group} plugin {name!r} not found"
        if self.available:
            message = f"{message}. Available: {', '.join(self.available)}."
        super().__init__(message)


_LOAD_ERRORS: dict[tuple[str, str, str, str | None, str], PluginLoadError] = {}
_LOADED: dict[tuple[str, str], Any] = {}


def _error_key(error: PluginLoadError) -> tuple[str, str, str, str | None, str]:
    return (error.group, error.name, error.value, error.distribution, error.error)


def _record_error(error: PluginLoadError) -> None:
    key = _error_key(error)
    _LOAD_ERRORS.setdefault(key, error)


def get_load_errors() -> tuple[PluginLoadError, ...]:
    return tuple(_LOAD_ERRORS.values())


def clear_load_errors(*, group: str | None = None, name: str | None = None) -> None:
    if group is None and name is None:
        _LOAD_ERRORS.clear()
        return
    remaining: dict[tuple[str, str, str, str | None, str], PluginLoadError] = {}
    for key, error in _LOAD_ERRORS.items():
        if group is not None and error.group != group:
            remaining[key] = error
            continue
        if name is not None and error.name != name:
            remaining[key] = error
            continue
    _LOAD_ERRORS.clear()
    _LOAD_ERRORS.update(remaining)


def reset_plugin_state() -> None:
    clear_load_errors()
    _LOADED.clear()


def _select_entrypoints(group: str) -> list[EntryPoint]:
    return list(entry_points().select(group=group))


def entrypoint_distribution_name(ep: EntryPoint) -> str | None:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return None
    name = getattr(dist, "name", None)
    if name:
        return name
    metadata = getattr(dist, "metadata", None)
    if metadata is None:
        return None
    try:
        return metadata["Name"]
    except (KeyError, TypeError):
        return None


def normalize_allowlist(allowlist: Iterable[str] | None) -> set[str] | None:
    if allowlist is None:
        return None
    cleaned = {
        _CANONICAL_NAME_RE.sub("-", item.strip()).lower()
        for item in allowlist
        if item and item.strip()
    }
    return cleaned or None


def is_entrypoint_allowed(ep: EntryPoint, allowlist: set[str] | None) -> bool:
    if allowlist is None:
        return True
    dist_name = entrypoint_distribution_name(ep)
    if dist_name is None:
        return False
    return _CANONICAL_NAME_RE.sub("-", dist_name).lower() in allowlist


def _entrypoint_sort_key(ep: EntryPoint) -> tuple[str, str, str]:
    dist = entrypoint_distribution_name(ep) or ""
    return (ep.name, dist, ep.value)


def _normalize_reserved(reserved: Iterable[str] | None) -> set[str] | None:
    if reserved is None:
        return None
    cleaned = {item.strip().lower() for item in reserved if item and item.strip()}
    return cleaned or None


def _discover_entrypoints(
    group: str,
    *,
    allowlist: Iterable[str] | None = None,
    reserved_ids: Iterable[str] | None = None,
) -> tuple[dict[str, EntryPoint], dict[str, list[EntryPoint]]]:
    allow = normalize_allowlist(allowlist)
    reserved = _normalize_reserved(reserved_ids)
    raw_eps = _select_entrypoints(group)
    eps = [ep for ep in raw_eps if is_entrypoint_allowed(ep, allow)]
    eps.sort(key=_entrypoint_sort_key)

    by_name: dict[str, EntryPoint] = {}
    duplicates: dict[str, list[EntryPoint]] = {}

    for ep in eps:
        if not is_valid_id(ep.name):
            _record_error(
                PluginLoadError(
                    group=group,
                    name=ep.name,
                    value=ep.value,
                    distribution=entrypoint_distribution_name(ep),
                    error=(f"invalid plugin id {ep.name!r}; must match {ID_PATTERN}"),
                )
            )
            continue
        if reserved is not None and ep.name.lower() in reserved:
            _record_error(
                PluginLoadError(
                    group=group,
                    name=ep.name,
                    value=ep.value,
                    distribution=entrypoint_distribution_name(ep),
                    error=f"reserved plugin id {ep.name!r} is not allowed",
                )
            )
            continue
        existing = by_name.get(ep.name)
        if existing is None:
            by_name[ep.name] = ep
            continue
        duplicates.setdefault(ep.name, [existing]).append(ep)

    for name, items in duplicates.items():
        providers = ", ".join(
            sorted(
                {entrypoint_distribution_name(item) or "<unknown>" for item in items}
            )
        )
        message = f"duplicate plugin id {name!r} from {providers}"
        for item in items:
            _record_error(
                PluginLoadError(
                    group=group,
                    name=name,
                    value=item.value,
                    distribution=entrypoint_distribution_name(item),
                    error=message,
                )
            )
        by_name.pop(name, None)

    return by_name, duplicates


def list_entrypoints(
    group: str,
    *,
    allowlist: Iterable[str] | None = None,
    reserved_ids: Iterable[str] | None = None,
) -> list[EntryPoint]:
    by_name, _ = _discover_entrypoints(
        group, allowlist=allowlist, reserved_ids=reserved_ids
    )
    return [by_name[name] for name in sorted(by_name)]


def list_ids(
    group: str,
    *,
    allowlist: Iterable[str] | None = None,
    reserved_ids: Iterable[str] | None = None,
) -> list[str]:
    return sorted(
        ep.name
        for ep in list_entrypoints(
            group, allowlist=allowlist, reserved_ids=reserved_ids
        )
    )


def load_entrypoint(
    group: str,
    name: str,
    *,
    allowlist: Iterable[str] | None = None,
    validator: Callable[[Any, EntryPoint], None] | None = None,
) -> Any:
    by_name, duplicates = _discover_entrypoints(group, allowlist=allowlist)
    if name in duplicates:
        items = duplicates[name]
        providers = ", ".join(
            sorted(
                {entrypoint_distribution_name(item) or "<unknown>" for item in items}
            )
        )
        error = PluginLoadError(
            group=group,
            name=name,
            value=items[0].value,
            distribution=entrypoint_distribution_name(items[0]),
            error=f"duplicate plugin id {name!r} from {providers}",
        )
        _record_error(error)
        raise PluginLoadFailed(error)

    ep = by_name.get(name)
    if ep is None:
        raise PluginNotFound(group, name, by_name)

    key = (group, name)
    if key in _LOADED:
        return _LOADED[key]

    try:
        loaded = ep.load()
        if validator is not None:
            validator(loaded, ep)
    except PluginLoadFailed:
        raise
    except Exception as exc:
        error = PluginLoadError(
            group=group,
            name=ep.name,
            value=ep.value,
            distribution=entrypoint_distribution_name(ep),
            error=str(exc),
        )
        _record_error(error)
        raise PluginLoadFailed(error) from exc

    _LOADED[key] = loaded
    clear_load_errors(group=group, name=name)
    return loaded


def load_plugin_backend(
    group: str,
    name: str,
    *,
    allowlist: Iterable[str] | None = None,
    validator: Callable[[Any, EntryPoint], None] | None = None,
    kind_label: str,
    required: bool = True,
) -> Any | None:
    try:
        return load_entrypoint(
            group,
            name,
            allowlist=allowlist,
            validator=validator,
        )
    except PluginNotFound as exc:
        if not required:
            return None
        if exc.available:
            available = ", ".join(exc.available)
            message = f"Unknown {kind_label} {name!r}. Available: {available}."
        else:
            message = f"Unknown {kind_label} {name!r}."
        from .config import ConfigError

        raise ConfigError(message) from exc
    except PluginLoadFailed as exc:
        from .config import ConfigError

        raise ConfigError(f"Failed to load {kind_label} {name!r}: {exc}") from exc
