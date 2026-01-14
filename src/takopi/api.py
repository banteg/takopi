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
from .progress import ActionState, ProgressState, ProgressTracker
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

# Plugin utilities
from .config import HOME_CONFIG_PATH, read_config, write_config
from .ids import RESERVED_COMMAND_IDS
from .logging import bind_run_context, clear_context, get_logger, suppress_logs
from .utils.paths import reset_run_base_dir, set_run_base_dir
from .scheduler import ThreadJob, ThreadScheduler
from .commands import get_command, list_command_ids
from .engines import list_backends
from .settings import load_settings
from .backends_helpers import install_issue

TAKOPI_PLUGIN_API_VERSION = 1

__all__ = [
    # Core types
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
    "ProgressState",
    "ProgressTracker",
    "ActionState",
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
    # Plugin utilities
    # -- Constants
    "HOME_CONFIG_PATH",
    "RESERVED_COMMAND_IDS",
    # -- Config
    "read_config",
    "write_config",
    # -- Logging
    "get_logger",
    "bind_run_context",
    "clear_context",
    "suppress_logs",
    # -- Path context
    "set_run_base_dir",
    "reset_run_base_dir",
    # -- Scheduler
    "ThreadJob",
    "ThreadScheduler",
    # -- Commands
    "get_command",
    "list_command_ids",
    # -- Discovery
    "list_backends",
    "load_settings",
    "install_issue",
]
