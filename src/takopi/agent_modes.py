from __future__ import annotations

from dataclasses import dataclass, field
import subprocess

from .logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AgentModeCapabilities:
    supports_agent: bool = False
    known_modes: tuple[str, ...] = ()
    shortcut_modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModeDiscoveryResult:
    supports_agent: frozenset[str] = field(default_factory=frozenset)
    known_modes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    shortcut_modes: tuple[str, ...] = ()


def probe_agent_support_via_help(cmd: str, timeout_s: float) -> AgentModeCapabilities:
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
            "agent_modes.help.failed",
            cmd=cmd,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return AgentModeCapabilities()
    except subprocess.TimeoutExpired:
        logger.info("agent_modes.help.timeout", cmd=cmd, timeout_s=timeout_s)
        return AgentModeCapabilities()
    output = f"{proc.stdout}\n{proc.stderr}".strip()
    if "--agent" not in output:
        return AgentModeCapabilities()
    return AgentModeCapabilities(supports_agent=True)
