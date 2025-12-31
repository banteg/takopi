from __future__ import annotations

from typing import Any, cast

from takopi.model import ActionKind, EngineId, LogLevel, ResumeToken, TakopiEvent


def session_started(engine: str, value: str, title: str = "Codex") -> TakopiEvent:
    engine_id = EngineId(engine)
    return cast(
        TakopiEvent,
        {
            "type": "session.started",
            "engine": engine_id,
            "resume": ResumeToken(engine=engine_id, value=value),
            "title": title,
        },
    )


def action_started(
    action_id: str,
    kind: ActionKind,
    title: str,
    detail: dict[str, Any] | None = None,
    engine: str = "codex",
) -> TakopiEvent:
    engine_id = EngineId(engine)
    return cast(
        TakopiEvent,
        {
            "type": "action.started",
            "engine": engine_id,
            "action": {
                "id": action_id,
                "kind": kind,
                "title": title,
                "detail": detail or {},
            },
        },
    )


def action_completed(
    action_id: str,
    kind: ActionKind,
    title: str,
    ok: bool,
    detail: dict[str, Any] | None = None,
    engine: str = "codex",
) -> TakopiEvent:
    engine_id = EngineId(engine)
    return cast(
        TakopiEvent,
        {
            "type": "action.completed",
            "engine": engine_id,
            "action": {
                "id": action_id,
                "kind": kind,
                "title": title,
                "detail": detail or {},
            },
            "ok": ok,
        },
    )


def log_event(
    message: str, level: LogLevel = "info", engine: str = "codex"
) -> TakopiEvent:
    engine_id = EngineId(engine)
    return cast(
        TakopiEvent,
        {
            "type": "log",
            "engine": engine_id,
            "level": level,
            "message": message,
        },
    )


def error_event(
    message: str, detail: str | None = None, engine: str = "codex"
) -> TakopiEvent:
    engine_id = EngineId(engine)
    event: dict[str, Any] = {
        "type": "error",
        "engine": engine_id,
        "message": message,
    }
    if detail:
        event["detail"] = detail
    return cast(TakopiEvent, event)
