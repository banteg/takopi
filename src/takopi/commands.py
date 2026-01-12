from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, overload, runtime_checkable

from .config import ConfigError
from .context import RunContext
from .ids import RESERVED_COMMAND_IDS
from .model import EngineId
from .plugins import COMMAND_GROUP, list_ids, load_plugin_backend
from .transport import MessageRef, RenderedMessage
from .transport_runtime import TransportRuntime

RunMode = Literal["emit", "capture"]


@dataclass(frozen=True, slots=True)
class RunRequest:
    prompt: str
    engine: EngineId | None = None
    context: RunContext | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    engine: EngineId
    message: RenderedMessage | None


class CommandExecutor(Protocol):
    async def send(
        self,
        message: RenderedMessage | str,
        *,
        reply_to: MessageRef | None = None,
        notify: bool = True,
    ) -> MessageRef | None: ...

    async def run_one(
        self, request: RunRequest, *, mode: RunMode = "emit"
    ) -> RunResult: ...

    async def run_many(
        self,
        requests: Sequence[RunRequest],
        *,
        mode: RunMode = "emit",
        parallel: bool = False,
    ) -> list[RunResult]: ...


@dataclass(frozen=True, slots=True)
class CommandContext:
    command: str
    text: str
    args_text: str
    args: tuple[str, ...]
    message: MessageRef
    reply_to: MessageRef | None
    reply_text: str | None
    config_path: Path | None
    plugin_config: dict[str, Any]
    runtime: TransportRuntime
    executor: CommandExecutor


@dataclass(frozen=True, slots=True)
class CommandResult:
    text: str
    notify: bool = True
    reply_to: MessageRef | None = None


@runtime_checkable
class CommandBackend(Protocol):
    id: str
    description: str

    async def handle(self, ctx: CommandContext) -> CommandResult | None: ...


def _validate_command_backend(backend: object, ep) -> None:
    if not isinstance(backend, CommandBackend):
        raise TypeError(f"{ep.value} is not a CommandBackend")
    if backend.id != ep.name:
        raise ValueError(
            f"{ep.value} command id {backend.id!r} does not match entrypoint {ep.name!r}"
        )


@overload
def get_command(
    command_id: str,
    *,
    allowlist: Iterable[str] | None = None,
    required: Literal[True] = True,
) -> CommandBackend: ...


@overload
def get_command(
    command_id: str,
    *,
    allowlist: Iterable[str] | None = None,
    required: Literal[False],
) -> CommandBackend | None: ...


def get_command(
    command_id: str,
    *,
    allowlist: Iterable[str] | None = None,
    required: bool = True,
) -> CommandBackend | None:
    if command_id.lower() in RESERVED_COMMAND_IDS:
        raise ConfigError(f"Command id {command_id!r} is reserved.")
    return load_plugin_backend(
        COMMAND_GROUP,
        command_id,
        allowlist=allowlist,
        validator=_validate_command_backend,
        kind_label="command",
        required=required,
    )


def list_command_ids(*, allowlist: Iterable[str] | None = None) -> list[str]:
    return list_ids(
        COMMAND_GROUP,
        allowlist=allowlist,
        reserved_ids=RESERVED_COMMAND_IDS,
    )
