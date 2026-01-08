"""Stable public API for Takopi plugins."""

from __future__ import annotations

from .backends import EngineBackend, EngineConfig, SetupIssue
from .commands import (
    CommandBackend,
    CommandContext,
    CommandExecutor,
    CommandResult,
    RunMode,
    RunRequest,
    RunResult,
)
from .config import ConfigError
from .context import RunContext
from .directives import DirectiveError
from .events import EventFactory
from .model import (
    Action,
    ActionEvent,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
)
from .presenter import Presenter
from .router import RunnerUnavailableError
from .runner import BaseRunner, JsonlSubprocessRunner, Runner
from .runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTask,
    RunningTasks,
    handle_message,
)
from .transport import MessageRef, RenderedMessage, SendOptions, Transport
from .transport_runtime import ResolvedMessage, ResolvedRunner, TransportRuntime
from .transports import SetupResult, TransportBackend

TAKOPI_PLUGIN_API_VERSION = 1

__all__ = [
    "Action",
    "ActionEvent",
    "BaseRunner",
    "CompletedEvent",
    "ConfigError",
    "CommandBackend",
    "CommandContext",
    "CommandExecutor",
    "CommandResult",
    "EngineBackend",
    "EngineConfig",
    "EngineId",
    "ExecBridgeConfig",
    "EventFactory",
    "IncomingMessage",
    "JsonlSubprocessRunner",
    "MessageRef",
    "DirectiveError",
    "Presenter",
    "RenderedMessage",
    "ResumeToken",
    "RunMode",
    "RunRequest",
    "RunResult",
    "ResolvedMessage",
    "ResolvedRunner",
    "RunContext",
    "Runner",
    "RunnerUnavailableError",
    "RunningTask",
    "RunningTasks",
    "SendOptions",
    "SetupIssue",
    "SetupResult",
    "StartedEvent",
    "TAKOPI_PLUGIN_API_VERSION",
    "Transport",
    "TransportBackend",
    "TransportRuntime",
    "handle_message",
]
