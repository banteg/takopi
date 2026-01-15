"""Response capture utilities for session management."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ResponseCapture"]


@dataclass(slots=True)
class ResponseCapture:
    """Mutable container to capture the final response text.

    This is used to capture the response from engine execution
    without modifying the core message flow.
    """

    text: str | None = None
