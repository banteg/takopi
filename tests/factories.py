from __future__ import annotations

from typing import Any

from takopi.model import ResumeToken, TakopiEvent


def session_started(engine: str, value: str, title: str = "Codex") -> TakopiEvent:
    return {
        "type": "session.started",
        "engine": engine,
        "resume": ResumeToken(engine=engine, value=value),
        "title": title,
    }


def action_started(
    action_id: str,
    kind: str,
    title: str,
    detail: dict[str, Any] | None = None,
    engine: str = "codex",
) -> TakopiEvent:
    return {
        "type": "action.started",
        "engine": engine,
        "action": {
            "id": action_id,
            "kind": kind,
            "title": title,
            "detail": detail or {},
        },
    }


def action_completed(
    action_id: str,
    kind: str,
    title: str,
    ok: bool,
    detail: dict[str, Any] | None = None,
    engine: str = "codex",
) -> TakopiEvent:
    return {
        "type": "action.completed",
        "engine": engine,
        "action": {
            "id": action_id,
            "kind": kind,
            "title": title,
            "detail": detail or {},
        },
        "ok": ok,
    }


def log_event(message: str, level: str = "info", engine: str = "codex") -> TakopiEvent:
    return {
        "type": "log",
        "engine": engine,
        "level": level,
        "message": message,
    }


def error_event(
    message: str, detail: str | None = None, engine: str = "codex"
) -> TakopiEvent:
    event: dict[str, Any] = {
        "type": "error",
        "engine": engine,
        "message": message,
    }
    if detail:
        event["detail"] = detail
    return event
