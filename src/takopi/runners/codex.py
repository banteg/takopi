from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..events import EventFactory
from ..logging import get_logger
from ..model import ActionPhase, EngineId, ResumeToken, TakopiEvent
from ..runner import JsonlSubprocessRunner, ResumeTokenMixin, Runner
from ..schemas import codex as codex_schema
from ..utils.paths import relativize_command

logger = get_logger(__name__)

ENGINE: EngineId = EngineId("codex")

_RESUME_RE = re.compile(r"(?im)^\s*`?codex\s+resume\s+(?P<token>[^`\s]+)`?\s*$")
_RECONNECTING_RE = re.compile(
    r"^Reconnecting\.{3}\s*(?P<attempt>\d+)/(?P<max>\d+)\s*$",
    re.IGNORECASE,
)


def _parse_reconnect_message(message: str) -> tuple[int, int] | None:
    match = _RECONNECTING_RE.match(message)
    if not match:
        return None
    try:
        attempt = int(match.group("attempt"))
        max_attempts = int(match.group("max"))
    except (TypeError, ValueError):
        return None
    return (attempt, max_attempts)


def _short_tool_name(server: str | None, tool: str | None) -> str:
    name = ".".join(part for part in (server, tool) if part)
    return name or "tool"


def _summarize_tool_result(result: Any) -> dict[str, Any] | None:
    if isinstance(result, codex_schema.McpToolCallItemResult):
        summary: dict[str, Any] = {}
        content = result.content
        if isinstance(content, list):
            summary["content_blocks"] = len(content)
        elif content is not None:
            summary["content_blocks"] = 1
        summary["has_structured"] = result.structured_content is not None
        return summary or None

    if isinstance(result, dict):
        summary = {}
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

    return None


def _normalize_change_list(changes: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for change in changes:
        path: str | None = None
        kind: str | None = None
        if isinstance(change, codex_schema.FileUpdateChange):
            path = change.path
            kind = change.kind
        elif isinstance(change, dict):
            path = change.get("path")
            kind = change.get("kind")
        if not isinstance(path, str) or not path:
            continue
        entry = {"path": path}
        if isinstance(kind, str) and kind:
            entry["kind"] = kind
        normalized.append(entry)
    return normalized


def _format_change_summary(changes: list[Any]) -> str:
    paths: list[str] = []
    for change in changes:
        if isinstance(change, codex_schema.FileUpdateChange):
            if change.path:
                paths.append(change.path)
            continue
        if isinstance(change, dict):
            path = change.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
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
        if isinstance(raw_item, codex_schema.TodoItem):
            total += 1
            if raw_item.completed:
                done += 1
                continue
            if next_text is None:
                next_text = raw_item.text
            continue
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


def _translate_item_event(
    phase: ActionPhase, item: codex_schema.ThreadItem, *, factory: EventFactory
) -> list[TakopiEvent]:
    match item:
        case codex_schema.AgentMessageItem():
            return []
        case codex_schema.ErrorItem(id=action_id, message=message):
            if phase != "completed":
                return []
            return [
                factory.action_completed(
                    action_id=action_id,
                    kind="warning",
                    title=message,
                    detail={"message": message},
                    ok=False,
                    message=message,
                    level="warning",
                ),
            ]
        case codex_schema.CommandExecutionItem(
            id=action_id,
            command=command,
            exit_code=exit_code,
            status=status,
        ):
            title = relativize_command(command)
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="command",
                        title=title,
                    )
                ]
            if phase == "completed":
                ok = status == "completed"
                if isinstance(exit_code, int):
                    ok = ok and exit_code == 0
                detail = {"exit_code": exit_code, "status": status}
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="command",
                        title=title,
                        detail=detail,
                        ok=ok,
                    ),
                ]
        case codex_schema.McpToolCallItem(
            id=action_id,
            server=server,
            tool=tool,
            arguments=arguments,
            status=status,
            result=result,
            error=error,
        ):
            title = _short_tool_name(server, tool)
            detail: dict[str, Any] = {
                "server": server,
                "tool": tool,
                "status": status,
                "arguments": arguments,
            }

            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="tool",
                        title=title,
                        detail=detail,
                    )
                ]
            if phase == "completed":
                ok = status == "completed" and error is None
                if error is not None:
                    detail["error_message"] = str(error.message)
                result_summary = _summarize_tool_result(result)
                if result_summary is not None:
                    detail["result_summary"] = result_summary
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="tool",
                        title=title,
                        detail=detail,
                        ok=ok,
                    ),
                ]
        case codex_schema.WebSearchItem(id=action_id, query=query):
            detail = {"query": query}
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="web_search",
                        title=query,
                        detail=detail,
                    )
                ]
            if phase == "completed":
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="web_search",
                        title=query,
                        detail=detail,
                        ok=True,
                    )
                ]
        case codex_schema.FileChangeItem(id=action_id, changes=changes, status=status):
            if phase != "completed":
                return []
            title = _format_change_summary(changes)
            normalized_changes = _normalize_change_list(changes)
            detail = {
                "changes": normalized_changes,
                "status": status,
                "error": None,
            }
            ok = status == "completed"
            return [
                factory.action_completed(
                    action_id=action_id,
                    kind="file_change",
                    title=title,
                    detail=detail,
                    ok=ok,
                )
            ]
        case codex_schema.TodoListItem(id=action_id, items=items):
            summary = _summarize_todo_list(items)
            title = _todo_title(summary)
            detail = {"done": summary.done, "total": summary.total}
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="note",
                        title=title,
                        detail=detail,
                    )
                ]
            if phase == "completed":
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="note",
                        title=title,
                        detail=detail,
                        ok=True,
                    )
                ]
        case codex_schema.ReasoningItem(id=action_id, text=text):
            if phase in {"started", "updated"}:
                return [
                    factory.action(
                        phase=phase,
                        action_id=action_id,
                        kind="note",
                        title=text,
                    )
                ]
            if phase == "completed":
                return [
                    factory.action_completed(
                        action_id=action_id,
                        kind="note",
                        title=text,
                        ok=True,
                    )
                ]
    return []


def translate_codex_event(
    event: codex_schema.ThreadEvent,
    *,
    title: str,
    factory: EventFactory,
) -> list[TakopiEvent]:
    match event:
        case codex_schema.ThreadStarted(thread_id=thread_id):
            token = ResumeToken(engine=ENGINE, value=thread_id)
            return [factory.started(token, title=title)]
        case codex_schema.ItemStarted(item=item):
            return _translate_item_event("started", item, factory=factory)
        case codex_schema.ItemUpdated(item=item):
            return _translate_item_event("updated", item, factory=factory)
        case codex_schema.ItemCompleted(item=item):
            return _translate_item_event("completed", item, factory=factory)
        case _:
            return []


@dataclass(slots=True)
class CodexRunState:
    factory: EventFactory
    note_seq: int = 0
    final_answer: str | None = None
    turn_index: int = 0


class CodexRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    engine: EngineId = ENGINE
    resume_re = _RESUME_RE
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
        return CodexRunState(factory=EventFactory(ENGINE))

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: CodexRunState,
    ) -> None:
        _ = state, prompt, resume

    def decode_jsonl(self, *, line: bytes) -> codex_schema.ThreadEvent:
        return codex_schema.decode_event(line)

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: CodexRunState,
    ) -> list[TakopiEvent]:
        _ = raw, line
        if isinstance(error, msgspec.DecodeError):
            self.get_logger().warning(
                "jsonl.msgspec.invalid",
                tag=self.tag(),
                error=str(error),
                error_type=error.__class__.__name__,
            )
            return []
        return super().decode_error_events(
            raw=raw,
            line=line,
            error=error,
            state=state,
        )

    def pipes_error_message(self) -> str:
        return "codex exec failed to open subprocess pipes"

    def translate(
        self,
        data: codex_schema.ThreadEvent,
        *,
        state: CodexRunState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        factory = state.factory
        match data:
            case codex_schema.StreamError(message=message):
                reconnect = _parse_reconnect_message(message)
                if reconnect is not None:
                    attempt, max_attempts = reconnect
                    phase: ActionPhase = "started" if attempt <= 1 else "updated"
                    return [
                        factory.action(
                            phase=phase,
                            action_id="codex.reconnect",
                            kind="note",
                            title=message,
                            detail={"attempt": attempt, "max": max_attempts},
                            level="info",
                        )
                    ]
                return [self.note_event(message, state=state, ok=False)]
            case codex_schema.TurnFailed(error=error):
                resume_for_completed = found_session or resume
                return [
                    factory.completed_error(
                        error=error.message,
                        answer=state.final_answer or "",
                        resume=resume_for_completed,
                    )
                ]
            case codex_schema.TurnStarted():
                action_id = f"turn_{state.turn_index}"
                state.turn_index += 1
                return [
                    factory.action_started(
                        action_id=action_id,
                        kind="turn",
                        title="turn started",
                    )
                ]
            case codex_schema.TurnCompleted(usage=usage):
                resume_for_completed = found_session or resume
                return [
                    factory.completed_ok(
                        answer=state.final_answer or "",
                        resume=resume_for_completed,
                        usage=msgspec.to_builtins(usage),
                    )
                ]
            case codex_schema.ItemCompleted(
                item=codex_schema.AgentMessageItem(text=text)
            ):
                if state.final_answer is None:
                    state.final_answer = text
                else:
                    logger.debug("codex.multiple_agent_messages")
                    state.final_answer = text
            case _:
                pass

        return translate_codex_event(
            data,
            title=self.session_title,
            factory=factory,
        )

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: CodexRunState,
    ) -> list[TakopiEvent]:
        message = f"codex exec failed (rc={rc})."
        resume_for_completed = found_session or resume
        return [
            self.note_event(
                message,
                state=state,
                ok=False,
            ),
            state.factory.completed_error(
                error=message,
                answer=state.final_answer or "",
                resume=resume_for_completed,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: CodexRunState,
    ) -> list[TakopiEvent]:
        if not found_session:
            message = "codex exec finished but no session_id/thread_id was captured"
            resume_for_completed = resume
            return [
                state.factory.completed_error(
                    error=message,
                    answer=state.final_answer or "",
                    resume=resume_for_completed,
                )
            ]
        logger.info("codex.session.completed", resume=found_session.value)
        return [
            state.factory.completed_ok(
                answer=state.final_answer or "",
                resume=found_session,
            )
        ]


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    codex_cmd = "codex"

    extra_args_value = config.get("extra_args")
    if extra_args_value is None:
        extra_args = ["-c", "notify=[]", "--skip-git-repo-check"]
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
