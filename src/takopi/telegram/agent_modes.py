from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from ..logging import get_logger
from ..transport_runtime import TransportRuntime

logger = get_logger(__name__)

_DEFAULT_OPENCODE_MODES: tuple[str, ...] = ("build", "plan")


@dataclass(frozen=True, slots=True)
class ModeDiscoveryResult:
    supports_agent: frozenset[str] = field(default_factory=frozenset)
    known_modes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    shortcut_modes: tuple[str, ...] = ()


def _run_help(cmd: str, *, timeout_s: float) -> str | None:
    try:
        proc = subprocess.run(
            [cmd, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except OSError as exc:
        logger.info(
            "telegram.mode_discovery.help.failed",
            cmd=cmd,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return None
    except subprocess.TimeoutExpired:
        logger.info(
            "telegram.mode_discovery.help.timeout", cmd=cmd, timeout_s=timeout_s
        )
        return None
    output = f"{proc.stdout}\n{proc.stderr}".strip()
    if not output:
        return None
    return output


def _supports_agent_flag(help_text: str | None) -> bool:
    if not help_text:
        return False
    return "--agent" in help_text


def _parse_opencode_agents(raw: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        match = re.match(r"^([a-z0-9_\-]{1,64})\s+\(", line.strip().lower())
        if match is None:
            continue
        mode = match.group(1)
        if mode in seen:
            continue
        seen.add(mode)
        found.append(mode)
    return tuple(found)


def _discover_opencode_modes(*, timeout_s: float) -> tuple[tuple[str, ...], bool]:
    try:
        proc = subprocess.run(
            ["opencode", "agent", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except OSError as exc:
        logger.info(
            "telegram.mode_discovery.opencode.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return _DEFAULT_OPENCODE_MODES, False
    except subprocess.TimeoutExpired:
        logger.info(
            "telegram.mode_discovery.opencode.timeout",
            timeout_s=timeout_s,
        )
        return _DEFAULT_OPENCODE_MODES, False

    raw = f"{proc.stdout}\n{proc.stderr}".strip()
    if proc.returncode != 0:
        logger.info(
            "telegram.mode_discovery.opencode.nonzero",
            rc=proc.returncode,
        )
        return _DEFAULT_OPENCODE_MODES, False
    parsed = _parse_opencode_agents(raw)
    if not parsed:
        logger.info("telegram.mode_discovery.opencode.empty")
        return _DEFAULT_OPENCODE_MODES, False
    return parsed, True


def discover_engine_modes(
    runtime: TransportRuntime,
    *,
    timeout_s: float = 8.0,
) -> ModeDiscoveryResult:
    known_modes: dict[str, tuple[str, ...]] = {}
    supports_agent: set[str] = set()
    shortcut_modes: tuple[str, ...] = ()

    available_engines = {engine.lower() for engine in runtime.available_engine_ids()}
    for engine in sorted(available_engines):
        if engine == "opencode":
            modes, discovered = _discover_opencode_modes(timeout_s=timeout_s)
            supports_agent.add(engine)
            known_modes[engine] = modes
            if discovered:
                shortcut_modes = modes
            continue

        help_text = _run_help(engine, timeout_s=timeout_s)
        if not _supports_agent_flag(help_text):
            continue
        supports_agent.add(engine)

    return ModeDiscoveryResult(
        supports_agent=frozenset(supports_agent),
        known_modes=known_modes,
        shortcut_modes=shortcut_modes,
    )
