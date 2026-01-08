"""Stable public API for Takopi plugins."""

from __future__ import annotations

from .backends import EngineBackend, EngineConfig, SetupIssue
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
from .runner import BaseRunner, JsonlSubprocessRunner, Runner
from .transport import MessageRef, RenderedMessage, SendOptions, Transport
from .transports import SetupResult, TransportBackend

TAKOPI_PLUGIN_API_VERSION = 1

__all__ = [
    "Action",
    "ActionEvent",
    "BaseRunner",
    "CompletedEvent",
    "EngineBackend",
    "EngineConfig",
    "EngineId",
    "EventFactory",
    "JsonlSubprocessRunner",
    "MessageRef",
    "Presenter",
    "RenderedMessage",
    "ResumeToken",
    "Runner",
    "SendOptions",
    "SetupIssue",
    "SetupResult",
    "StartedEvent",
    "TAKOPI_PLUGIN_API_VERSION",
    "Transport",
    "TransportBackend",
]
