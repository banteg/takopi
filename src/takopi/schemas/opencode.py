"""Msgspec models and decoder for opencode --format json output."""

from __future__ import annotations

from typing import Any

import msgspec


class _Event(msgspec.Struct, tag_field="type", forbid_unknown_fields=False):
    pass


class StepStart(_Event, tag="step_start"):
    timestamp: int | None = None
    sessionID: str | None = None
    part: dict[str, Any] | None = None


class StepFinish(_Event, tag="step_finish"):
    timestamp: int | None = None
    sessionID: str | None = None
    part: dict[str, Any] | None = None


class ToolUse(_Event, tag="tool_use"):
    timestamp: int | None = None
    sessionID: str | None = None
    part: dict[str, Any] | None = None


class Text(_Event, tag="text"):
    timestamp: int | None = None
    sessionID: str | None = None
    part: dict[str, Any] | None = None


class Error(_Event, tag="error"):
    timestamp: int | None = None
    sessionID: str | None = None
    error: Any = None
    message: Any = None


type OpenCodeEvent = StepStart | StepFinish | ToolUse | Text | Error

_DECODER = msgspec.json.Decoder(OpenCodeEvent)


def decode_event(line: str | bytes) -> OpenCodeEvent:
    return _DECODER.decode(line)
