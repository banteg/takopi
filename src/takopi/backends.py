from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .agent_modes import AgentModeCapabilities

if TYPE_CHECKING:
    from .runner import Runner

EngineConfig = dict[str, Any]
AgentModeProbe = Callable[[float], AgentModeCapabilities]


@dataclass(frozen=True, slots=True)
class SetupIssue:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EngineBackend:
    id: str
    build_runner: Callable[[EngineConfig, Path], Runner]
    cli_cmd: str | None = None
    install_cmd: str | None = None
    discover_agent_modes: AgentModeProbe | None = None
