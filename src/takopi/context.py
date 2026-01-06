from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunContext:
    project: str | None = None
    branch: str | None = None
