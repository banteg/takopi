from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .runner import Runner

EngineConfig = dict[str, Any]


@dataclass(frozen=True, slots=True)
class SetupIssue:
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EngineBackend:
    id: str
    check_setup: Callable[[EngineConfig, Path], list[SetupIssue]]
    build_runner: Callable[[EngineConfig, Path], Runner]
    startup_message: Callable[[str], str]
    install_cmds: tuple[str, ...] | None = None
