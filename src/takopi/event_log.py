from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logging import _redact_value


@dataclass(frozen=True, slots=True)
class TakopiLogEvent:
    ts: str
    kind: str
    chat_id: int
    thread_id: int | None
    message_id: int | None
    engine: str | None
    project: str | None
    text: str
    meta: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    _ensure_parent(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    return _redact_value(payload, memo={})


def record_event(
    *,
    path: Path,
    kind: str,
    chat_id: int,
    thread_id: int | None,
    message_id: int | None,
    engine: str | None,
    project: str | None,
    text: str,
    meta: dict[str, Any] | None,
    max_text_chars: int,
) -> None:
    redacted_text = _redact_value(text, memo={})
    if not isinstance(redacted_text, str):
        redacted_text = str(redacted_text)
    event = TakopiLogEvent(
        ts=_utc_now(),
        kind=kind,
        chat_id=chat_id,
        thread_id=thread_id,
        message_id=message_id,
        engine=engine,
        project=project,
        text=_truncate(redacted_text, max_text_chars),
        meta=meta or {},
    )
    payload = _redact(asdict(event))
    _write_jsonl(path, payload)
