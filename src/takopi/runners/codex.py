from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..model import (
    Action,
    ActionEvent,
    ActionKind,
    ActionLevel,
    ActionPhase,
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..utils.paths import relativize_command

logger = logging.getLogger(__name__)

ENGINE: EngineId = EngineId("codex")
STDERR_TAIL_LINES = 200

_ACTION_KIND_MAP: dict[str, ActionKind] = {
    "command_execution": "command",
    "mcp_tool_call": "tool",
    "tool_call": "tool",
    "web_search": "web_search",
    "file_change": "file_change",
    "reasoning": "note",
    "todo_list": "note",
}

_RESUME_RE = re.compile(r"(?im)^\s*`?codex\s+resume\s+(?P<token>[^`\s]+)`?\s*$")


def _started_event(token: ResumeToken, *, title: str) -> StartedEvent:
    return StartedEvent(engine=token.engine, resume=token, title=title)


def _completed_event(
    *,
    resume: ResumeToken | None,
    ok: bool,
    answer: str,
    error: str | None = None,
    usage: dict[str, Any] | None = None,
) -> TakopiEvent:
    return CompletedEvent(
        engine=ENGINE,
        ok=ok,
        answer=answer,
        resume=resume,
        error=error,
        usage=usage,
    )


def _action_event(
    *,
    phase: ActionPhase,
    action_id: str,
    kind: ActionKind,
    title: str,
    detail: dict[str, Any] | None = None,
    ok: bool | None = None,
    message: str | None = None,
    level: ActionLevel | None = None,
) -> TakopiEvent:
    action = Action(
        id=action_id,
        kind=kind,
        title=title,
        detail=detail or {},
    )
    return ActionEvent(
        engine=ENGINE,
        action=action,
        phase=phase,
        ok=ok,
        message=message,
        level=level,
    )


def _short_tool_name(item: dict[str, Any]) -> str:
    name = ".".join(part for part in (item.get("server"), item.get("tool")) if part)
    return name or "tool"


def _summarize_tool_result(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    summary: dict[str, Any] = {}
    content = result.get("content")
    if isinstance(content, list):
        summary["content_blocks"] = len(content)
    elif content is not None:
        summary["content_blocks"] = 1

    structured_key: str | None = None
    if "structured_content" in result:
        structured_key = "structured_content"
    elif "structured" in result:
        structured_key = "structured"

    if structured_key is not None:
        summary["has_structured"] = result.get(structured_key) is not None
    return summary or None


def _format_change_summary(item: dict[str, Any]) -> str:
    changes = item.get("changes") or []
    paths = [c.get("path") for c in changes if c.get("path")]
    if not paths:
        total = len(changes)
        if total <= 0:
            return "files"
        return f"{total} files"
    return ", ".join(str(path) for path in paths)


@dataclass(frozen=True, slots=True)
class _TodoSummary:
    done: int
    total: int
    next_text: str | None


def _summarize_todo_list(items: Any) -> _TodoSummary:
    if not isinstance(items, list):
        return _TodoSummary(done=0, total=0, next_text=None)

    done = 0
    total = 0
    next_text: str | None = None

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        total += 1
        completed = raw_item.get("completed") is True
        if completed:
            done += 1
            continue
        if next_text is None:
            text = raw_item.get("text")
            next_text = str(text) if text is not None else None

    return _TodoSummary(done=done, total=total, next_text=next_text)


def _todo_title(summary: _TodoSummary) -> str:
    if summary.total <= 0:
        return "todo"
    if summary.next_text:
        return f"todo {summary.done}/{summary.total}: {summary.next_text}"
    return f"todo {summary.done}/{summary.total}: done"


def _translate_item_event(etype: str, item: dict[str, Any]) -> list[TakopiEvent]:
    item_type = item.get("type") or item.get("item_type")
    if item_type == "assistant_message":
        item_type = "agent_message"

    if not item_type:
        return []

    if item_type == "agent_message":
        return []

    action_id = item.get("id")
    if not isinstance(action_id, str) or not action_id:
        logger.debug("[codex] missing item id in codex event: %r", item)
        return []

    phase = cast(ActionPhase, etype.split(".")[-1])

    if item_type == "error":
        if phase != "completed":
            return []
        message = str(item.get("message") or "codex item error")
        return [
            _action_event(
                phase="completed",
                action_id=action_id,
                kind="warning",
                title=message,
                detail={"message": message},
                ok=False,
                message=message,
                level="warning",
            )
        ]

    kind = _ACTION_KIND_MAP.get(item_type)
    if kind is None:
        return []

    if kind == "command":
        title = relativize_command(str(item.get("command") or ""))
        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                )
            ]
        if phase == "completed":
            exit_code = item.get("exit_code")
            ok = item.get("status") != "failed"
            if isinstance(exit_code, int):
                ok = ok and exit_code == 0
            detail = {
                "exit_code": exit_code,
                "status": item.get("status"),
            }
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=ok,
                )
            ]

    if kind == "tool":
        tool_name = _short_tool_name(item)
        title = tool_name
        detail = {
            "server": item.get("server"),
            "tool": item.get("tool"),
            "status": item.get("status"),
        }
        if "arguments" in item:
            detail["arguments"] = item.get("arguments")
        if item_type == "tool_call":
            name = item.get("name")
            tool_name = str(name) if name else "tool"
            title = tool_name
            detail = {"name": name, "status": item.get("status")}
            if "arguments" in item:
                detail["arguments"] = item.get("arguments")

        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]
        if phase == "completed":
            ok = item.get("status") != "failed" and not item.get("error")
            error = item.get("error")
            if error:
                detail["error_message"] = str(
                    error.get("message") if isinstance(error, dict) else error
                )
            result_summary = _summarize_tool_result(item.get("result"))
            if result_summary is not None:
                detail["result_summary"] = result_summary
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=ok,
                )
            ]

    if kind == "web_search":
        title = str(item.get("query") or "")
        detail = {"query": item.get("query")}
        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]
        if phase == "completed":
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=True,
                )
            ]

    if kind == "file_change":
        if phase != "completed":
            return []
        title = _format_change_summary(item)
        detail = {
            "changes": item.get("changes") or [],
            "status": item.get("status"),
            "error": item.get("error"),
        }
        ok = item.get("status") != "failed"
        return [
            _action_event(
                phase="completed",
                action_id=action_id,
                kind=kind,
                title=title,
                detail=detail,
                ok=ok,
            )
        ]

    if kind == "note":
        if item_type == "todo_list":
            summary = _summarize_todo_list(item.get("items"))
            title = _todo_title(summary)
            detail = {"done": summary.done, "total": summary.total}
        else:
            title = str(item.get("text") or "")
            detail = None

        if phase in {"started", "updated"}:
            return [
                _action_event(
                    phase=phase,
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                )
            ]
        if phase == "completed":
            return [
                _action_event(
                    phase="completed",
                    action_id=action_id,
                    kind=kind,
                    title=title,
                    detail=detail,
                    ok=True,
                )
            ]

    return []


def translate_codex_event(event: dict[str, Any], *, title: str) -> list[TakopiEvent]:
    etype = event.get("type")
    if etype == "thread.started":
        thread_id = event.get("thread_id")
        if thread_id:
            token = ResumeToken(engine=ENGINE, value=str(thread_id))
            return [_started_event(token, title=title)]
        logger.debug("[codex] codex thread.started missing thread_id: %r", event)
        return []

    if etype in {"item.started", "item.updated", "item.completed"}:
        item = event.get("item") or {}
        return _translate_item_event(etype, item)

    return []


@dataclass(slots=True)
class CodexRunState:
    note_seq: int = 0
    final_answer: str | None = None
    turn_index: int = 0


class CodexRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re = _RESUME_RE
    stderr_tail_lines = STDERR_TAIL_LINES
    logger = logger

    def __init__(
        self,
        *,
        codex_cmd: str,
        extra_args: list[str],
        title: str = "Codex",
    ) -> None:
        self.codex_cmd = codex_cmd
        self.extra_args = extra_args
        self.session_title = title

    def command(self) -> str:
        return self.codex_cmd

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        _ = prompt, state
        args = [*self.extra_args, "exec", "--json"]
        if resume:
            args.extend(["resume", resume.value, "-"])
        else:
            args.append("-")
        return args

    def new_state(self, prompt: str, resume: ResumeToken | None) -> CodexRunState:
        _ = prompt, resume
        return CodexRunState()

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: CodexRunState,
    ) -> None:
        _ = state
        logger.info("[codex] start run resume=%r", resume.value if resume else None)
        logger.debug("[codex] prompt: %s", prompt)

    def pipes_error_message(self) -> str:
        return "codex exec failed to open subprocess pipes"

    def handle_started_event(
        self,
        event: StartedEvent,
        *,
        expected_session: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> tuple[ResumeToken | None, bool]:
        if event.engine != ENGINE:
            raise RuntimeError(
                f"codex emitted session token for engine {event.engine!r}"
            )
        if expected_session is not None and event.resume != expected_session:
            message = "codex emitted a different session id than expected"
            raise RuntimeError(message)
        if found_session is None:
            return event.resume, True
        if event.resume != found_session:
            message = "codex emitted a different session id than expected"
            raise RuntimeError(message)
        return found_session, False

    def translate(
        self,
        data: dict[str, Any],
        *,
        state: CodexRunState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        etype = data.get("type")
        if etype == "error":
            message = str(data.get("message") or "codex error")
            fatal_flag = data.get("fatal")
            fatal = fatal_flag is True or fatal_flag is None
            if fatal:
                resume_for_completed = found_session or resume
                return [
                    _completed_event(
                        resume=resume_for_completed,
                        ok=False,
                        answer=state.final_answer or "",
                        error=message,
                    )
                ]
            return [
                self.note_event(
                    message,
                    state=state,
                    ok=False,
                    detail={"code": data.get("code"), "fatal": data.get("fatal")},
                )
            ]
        if etype == "turn.failed":
            error = data.get("error") or {}
            message = str(error.get("message") or "codex turn failed")
            resume_for_completed = found_session or resume
            return [
                _completed_event(
                    resume=resume_for_completed,
                    ok=False,
                    answer=state.final_answer or "",
                    error=message,
                )
            ]
        if etype == "turn.rate_limited":
            retry_ms = data.get("retry_after_ms")
            message = "rate limited"
            if isinstance(retry_ms, int):
                message = f"rate limited (retry after {retry_ms}ms)"
            return [self.note_event(message, state=state, ok=False)]
        if etype == "turn.started":
            action_id = f"turn_{state.turn_index}"
            state.turn_index += 1
            return [
                _action_event(
                    phase="started",
                    action_id=action_id,
                    kind="turn",
                    title="turn started",
                )
            ]
        if etype == "turn.completed":
            resume_for_completed = found_session or resume
            return [
                _completed_event(
                    resume=resume_for_completed,
                    ok=True,
                    answer=state.final_answer or "",
                    usage=data.get("usage"),
                )
            ]

        if data.get("type") == "item.completed":
            item = data.get("item") or {}
            item_type = item.get("type") or item.get("item_type")
            if item_type == "assistant_message":
                item_type = "agent_message"
            if item_type == "agent_message" and isinstance(item.get("text"), str):
                if state.final_answer is None:
                    state.final_answer = item["text"]
                else:
                    logger.debug(
                        "[codex] emitted multiple agent messages; using the last one"
                    )
                    state.final_answer = item["text"]

        return translate_codex_event(data, title=self.session_title)

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        stderr_tail: str,
        state: CodexRunState,
    ) -> list[TakopiEvent]:
        message = f"codex exec failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(
                message,
                state=state,
                ok=False,
                detail={"stderr_tail": stderr_tail},
            ),
            _completed_event(
                resume=resume_for_completed,
                ok=False,
                answer=state.final_answer or "",
                error=message,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        stderr_tail: str,
        state: CodexRunState,
    ) -> list[TakopiEvent]:
        _ = stderr_tail
        if not found_session:
            message = "codex exec finished but no session_id/thread_id was captured"
            resume_for_completed = resume
            return [
                _completed_event(
                    resume=resume_for_completed,
                    ok=False,
                    answer=state.final_answer or "",
                    error=message,
                )
            ]
        logger.info("[codex] done run session=%s", found_session.value)
        return [
            _completed_event(
                resume=found_session,
                ok=True,
                answer=state.final_answer or "",
            )
        ]


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    codex_cmd = "codex"

    extra_args_value = config.get("extra_args")
    if extra_args_value is None:
        extra_args = ["-c", "notify=[]"]
    elif isinstance(extra_args_value, list) and all(
        isinstance(item, str) for item in extra_args_value
    ):
        extra_args = list(extra_args_value)
    else:
        raise ConfigError(
            f"Invalid `codex.extra_args` in {config_path}; expected a list of strings."
        )

    title = "Codex"
    profile_value = config.get("profile")
    if profile_value:
        if not isinstance(profile_value, str):
            raise ConfigError(
                f"Invalid `codex.profile` in {config_path}; expected a string."
            )
        extra_args.extend(["--profile", profile_value])
        title = profile_value

    return CodexRunner(codex_cmd=codex_cmd, extra_args=extra_args, title=title)


BACKEND = EngineBackend(
    id="codex",
    build_runner=build_runner,
    install_cmd="npm install -g @openai/codex",
)
