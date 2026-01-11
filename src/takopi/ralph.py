"""Ralph loop: iterative self-review execution.

True ralph: fresh context each iteration, state lives in files.
Each iteration re-reads the codebase to understand current state.
No session continuity - prevents context pollution and drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RALPH_ITERATION_PROMPT = "review your previous work and continue"

RALPH_COMPLETE_PATTERN = re.compile(r"RALPH_COMPLETE:\s*(.+)", re.MULTILINE)


def strip_ralph_complete(text: str) -> str:
    """Remove RALPH_COMPLETE: lines from the answer."""
    return RALPH_COMPLETE_PATTERN.sub("", text).strip()


def check_ralph_complete(text: str) -> bool:
    """Check if the answer contains RALPH_COMPLETE signal."""
    return RALPH_COMPLETE_PATTERN.search(text) is not None


@dataclass(slots=True)
class RalphLoopState:
    """Tracks state across ralph loop iterations."""

    max_iterations: int
    current_iteration: int = 0
    completed: bool = False
