"""Stable public API for Takopi plugins."""

from __future__ import annotations

from .backends import EngineBackend, EngineConfig, SetupIssue
from .config import ConfigError, ProjectConfig, ProjectsConfig
from .context import RunContext
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
from .router import AutoRouter, RunnerEntry, RunnerUnavailableError
from .runner import BaseRunner, JsonlSubprocessRunner, Runner
from .runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTask,
    RunningTasks,
    handle_message,
)
from .settings import TakopiSettings
from .transport import MessageRef, RenderedMessage, SendOptions, Transport
from .transports import SetupResult, TransportBackend

TAKOPI_PLUGIN_API_VERSION = 1

__all__ = [
    "Action",
    "ActionEvent",
    "AutoRouter",
    "BaseRunner",
    "CompletedEvent",
    "ConfigError",
    "EngineBackend",
    "EngineConfig",
    "EngineId",
    "ExecBridgeConfig",
    "EventFactory",
    "IncomingMessage",
    "JsonlSubprocessRunner",
    "MessageRef",
    "ProjectConfig",
    "ProjectsConfig",
    "Presenter",
    "RenderedMessage",
    "ResumeToken",
    "RunContext",
    "Runner",
    "RunnerEntry",
    "RunnerUnavailableError",
    "RunningTask",
    "RunningTasks",
    "SendOptions",
    "SetupIssue",
    "SetupResult",
    "StartedEvent",
    "TAKOPI_PLUGIN_API_VERSION",
    "TakopiSettings",
    "Transport",
    "TransportBackend",
    "handle_message",
]
