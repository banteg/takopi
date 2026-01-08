from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import re

import anyio

from ..config import ProjectsConfig, empty_projects_config
from ..context import RunContext
from ..runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage as RunnerIncomingMessage,
    RunningTask,
    RunningTasks,
    handle_message,
)
from ..logging import bind_run_context, clear_context, get_logger
from ..markdown import MarkdownFormatter, MarkdownParts
from ..model import EngineId, ResumeToken
from ..progress import ProgressState, ProgressTracker
from ..router import AutoRouter, RunnerUnavailableError
from ..runner import Runner
from ..scheduler import ThreadJob, ThreadScheduler
from ..transport import (
    IncomingMessage as TransportIncomingMessage,
    MessageRef,
    RenderedMessage,
    SendOptions,
    Transport,
)
from ..utils.paths import reset_run_base_dir, set_run_base_dir
from ..worktrees import WorktreeError, resolve_run_cwd
from .client import BotClient, poll_incoming
from .render import prepare_telegram

logger = get_logger(__name__)

_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_MAX_BOT_COMMANDS = 100


def _is_valid_bot_command(command: str) -> bool:
    return bool(_COMMAND_RE.fullmatch(command))


def _is_cancel_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")


def _strip_engine_command(
    text: str, *, engine_ids: tuple[EngineId, ...]
) -> tuple[str, EngineId | None]:
    if not text:
        return text, None

    if not engine_ids:
        return text, None

    engine_map = {engine.lower(): engine for engine in engine_ids}
    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if idx is None:
        return text, None

    line = lines[idx].lstrip()
    if not line.startswith("/"):
        return text, None

    parts = line.split(maxsplit=1)
    command = parts[0][1:]
    if "@" in command:
        command = command.split("@", 1)[0]
    engine = engine_map.get(command.lower())
    if engine is None:
        return text, None

    remainder = parts[1] if len(parts) > 1 else ""
    if remainder:
        lines[idx] = remainder
    else:
        lines.pop(idx)
    return "\n".join(lines).strip(), engine


@dataclass(frozen=True, slots=True)
class ParsedDirectives:
    prompt: str
    engine: EngineId | None
    project: str | None
    branch: str | None


@dataclass(frozen=True, slots=True)
class ResolvedMessage:
    prompt: str
    resume_token: ResumeToken | None
    engine_override: EngineId | None
    context: RunContext | None


class DirectiveError(RuntimeError):
    pass


def _parse_directives(
    text: str,
    *,
    engine_ids: tuple[EngineId, ...],
    projects: ProjectsConfig,
) -> ParsedDirectives:
    if not text:
        return ParsedDirectives(prompt="", engine=None, project=None, branch=None)

    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if idx is None:
        return ParsedDirectives(prompt=text, engine=None, project=None, branch=None)

    line = lines[idx].lstrip()
    tokens = line.split()
    if not tokens:
        return ParsedDirectives(prompt=text, engine=None, project=None, branch=None)

    engine_map = {engine.lower(): engine for engine in engine_ids}
    project_map = {alias.lower(): alias for alias in projects.projects}

    engine: EngineId | None = None
    project: str | None = None
    branch: str | None = None
    consumed = 0

    for token in tokens:
        if token.startswith("/"):
            name = token[1:]
            if "@" in name:
                name = name.split("@", 1)[0]
            if not name:
                break
            key = name.lower()
            engine_candidate = engine_map.get(key)
            project_candidate = project_map.get(key)
            if engine_candidate is not None:
                if engine is not None:
                    raise DirectiveError("multiple engine directives")
                engine = engine_candidate
                consumed += 1
                continue
            if project_candidate is not None:
                if project is not None:
                    raise DirectiveError("multiple project directives")
                project = project_candidate
                consumed += 1
                continue
            break
        if token.startswith("@"):
            value = token[1:]
            if not value:
                break
            if branch is not None:
                raise DirectiveError("multiple @branch directives")
            branch = value
            consumed += 1
            continue
        break

    if consumed == 0:
        return ParsedDirectives(prompt=text, engine=None, project=None, branch=None)

    if consumed < len(tokens):
        remainder = " ".join(tokens[consumed:])
        lines[idx] = remainder
    else:
        lines.pop(idx)

    prompt = "\n".join(lines).strip()
    return ParsedDirectives(
        prompt=prompt, engine=engine, project=project, branch=branch
    )


def _parse_ctx_line(text: str | None, *, projects: ProjectsConfig) -> RunContext | None:
    if not text:
        return None
    ctx: RunContext | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 1:
            stripped = stripped[1:-1].strip()
        elif stripped.startswith("`"):
            stripped = stripped[1:].strip()
        elif stripped.endswith("`"):
            stripped = stripped[:-1].strip()
        if not stripped.lower().startswith("ctx:"):
            continue
        content = stripped.split(":", 1)[1].strip()
        if not content:
            continue
        tokens = content.split()
        if not tokens:
            continue
        project = tokens[0]
        branch = None
        if len(tokens) >= 2:
            if tokens[1] == "@" and len(tokens) >= 3:
                branch = tokens[2]
            elif tokens[1].startswith("@"):
                branch = tokens[1][1:]
        project_key = project.lower()
        if project_key not in projects.projects:
            raise DirectiveError(f"unknown project {project!r} in ctx line")
        ctx = RunContext(project=project_key, branch=branch)
    return ctx


def _format_context_line(
    context: RunContext | None, *, projects: ProjectsConfig
) -> str | None:
    if context is None or context.project is None:
        return None
    project_cfg = projects.projects.get(context.project)
    alias = project_cfg.alias if project_cfg is not None else context.project
    if context.branch:
        return f"`ctx: {alias} @ {context.branch}`"
    return f"`ctx: {alias}`"


def _resolve_message(
    *,
    text: str,
    reply_text: str | None,
    router: AutoRouter,
    projects: ProjectsConfig,
) -> ResolvedMessage:
    directives = _parse_directives(
        text,
        engine_ids=router.engine_ids,
        projects=projects,
    )
    reply_ctx = _parse_ctx_line(reply_text, projects=projects)
    resume_token = router.resolve_resume(directives.prompt, reply_text)

    if resume_token is not None:
        return ResolvedMessage(
            prompt=directives.prompt,
            resume_token=resume_token,
            engine_override=None,
            context=reply_ctx,
        )

    if reply_ctx is not None:
        engine_override = None
        if reply_ctx.project is not None:
            project = projects.projects.get(reply_ctx.project)
            if project is not None and project.default_engine is not None:
                engine_override = project.default_engine
        return ResolvedMessage(
            prompt=directives.prompt,
            resume_token=None,
            engine_override=engine_override,
            context=reply_ctx,
        )

    project_key = directives.project
    if project_key is None and projects.default_project is not None:
        project_key = projects.default_project

    context = None
    if project_key is not None or directives.branch is not None:
        context = RunContext(project=project_key, branch=directives.branch)

    engine_override = directives.engine
    if engine_override is None and project_key is not None:
        project = projects.projects.get(project_key)
        if project is not None and project.default_engine is not None:
            engine_override = project.default_engine

    return ResolvedMessage(
        prompt=directives.prompt,
        resume_token=None,
        engine_override=engine_override,
        context=context,
    )


def _build_bot_commands(
    router: AutoRouter, projects: ProjectsConfig
) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in router.available_entries:
        cmd = entry.engine.lower()
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": f"use agent: {cmd}"})
        seen.add(cmd)
    for alias, project in projects.projects.items():
        cmd = alias.lower()
        if cmd in seen:
            continue
        if not _is_valid_bot_command(cmd):
            logger.debug(
                "startup.command_menu.skip_project",
                alias=project.alias,
            )
            continue
        commands.append({"command": cmd, "description": f"work on: {cmd}"})
        seen.add(cmd)
    if "cancel" not in seen:
        commands.append({"command": "cancel", "description": "cancel run"})
    if len(commands) > _MAX_BOT_COMMANDS:
        logger.warning(
            "startup.command_menu.too_many",
            count=len(commands),
            limit=_MAX_BOT_COMMANDS,
        )
        commands = commands[:_MAX_BOT_COMMANDS]
        if not any(cmd["command"] == "cancel" for cmd in commands):
            commands[-1] = {"command": "cancel", "description": "cancel run"}
    return commands


async def _set_command_menu(cfg: TelegramBridgeConfig) -> None:
    commands = _build_bot_commands(cfg.router, cfg.projects)
    if not commands:
        return
    try:
        ok = await cfg.bot.set_my_commands(commands)
    except Exception as exc:
        logger.info(
            "startup.command_menu.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return
    if not ok:
        logger.info("startup.command_menu.rejected")
        return
    logger.info(
        "startup.command_menu.updated",
        commands=[cmd["command"] for cmd in commands],
    )


class TelegramPresenter:
    def __init__(self, *, formatter: MarkdownFormatter | None = None) -> None:
        self._formatter = formatter or MarkdownFormatter()

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        parts = self._formatter.render_progress_parts(
            state, elapsed_s=elapsed_s, label=label
        )
        text, entities = prepare_telegram(parts)
        return RenderedMessage(text=text, extra={"entities": entities})

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        parts = self._formatter.render_final_parts(
            state, elapsed_s=elapsed_s, status=status, answer=answer
        )
        text, entities = prepare_telegram(parts)
        return RenderedMessage(text=text, extra={"entities": entities})


def _as_int(value: int | str, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Telegram {label} must be int")
    return value


class TelegramTransport:
    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    async def close(self) -> None:
        await self._bot.close()

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        chat_id = _as_int(channel_id, label="chat_id")
        reply_to_message_id: int | None = None
        replace_message_id: int | None = None
        disable_notification = None
        if options is not None:
            disable_notification = not options.notify
            if options.reply_to is not None:
                reply_to_message_id = _as_int(
                    options.reply_to.message_id, label="reply_to_message_id"
                )
            if options.replace is not None:
                replace_message_id = _as_int(
                    options.replace.message_id, label="replace_message_id"
                )
        entities = message.extra.get("entities")
        parse_mode = message.extra.get("parse_mode")
        sent = await self._bot.send_message(
            chat_id=chat_id,
            text=message.text,
            reply_to_message_id=reply_to_message_id,
            disable_notification=disable_notification,
            entities=entities,
            parse_mode=parse_mode,
            replace_message_id=replace_message_id,
        )
        if sent is None:
            return None
        message_id = sent.get("message_id")
        if message_id is None:
            return None
        return MessageRef(
            channel_id=chat_id,
            message_id=_as_int(message_id, label="message_id"),
            raw=sent,
        )

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef | None:
        chat_id = _as_int(ref.channel_id, label="chat_id")
        message_id = _as_int(ref.message_id, label="message_id")
        entities = message.extra.get("entities")
        parse_mode = message.extra.get("parse_mode")
        edited = await self._bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message.text,
            entities=entities,
            parse_mode=parse_mode,
            wait=wait,
        )
        if edited is None:
            return ref if not wait else None
        message_id = edited.get("message_id", message_id)
        return MessageRef(
            channel_id=chat_id,
            message_id=_as_int(message_id, label="message_id"),
            raw=edited,
        )

    async def delete(self, *, ref: MessageRef) -> bool:
        return await self._bot.delete_message(
            chat_id=_as_int(ref.channel_id, label="chat_id"),
            message_id=_as_int(ref.message_id, label="message_id"),
        )


@dataclass(frozen=True)
class TelegramBridgeConfig:
    bot: BotClient
    router: AutoRouter
    chat_id: int
    startup_msg: str
    exec_cfg: ExecBridgeConfig
    projects: ProjectsConfig = field(default_factory=empty_projects_config)


async def _send_plain(
    transport: Transport,
    *,
    chat_id: int,
    user_msg_id: int,
    text: str,
    notify: bool = True,
) -> None:
    reply_to = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    await transport.send(
        channel_id=chat_id,
        message=RenderedMessage(text=text),
        options=SendOptions(reply_to=reply_to, notify=notify),
    )


async def _send_startup(cfg: TelegramBridgeConfig) -> None:
    logger.debug("startup.message", text=cfg.startup_msg)
    parts = MarkdownParts(header=cfg.startup_msg)
    text, entities = prepare_telegram(parts)
    message = RenderedMessage(text=text, extra={"entities": entities})
    sent = await cfg.exec_cfg.transport.send(
        channel_id=cfg.chat_id,
        message=message,
    )
    if sent is not None:
        logger.info("startup.sent", chat_id=cfg.chat_id)


async def _drain_backlog(cfg: TelegramBridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=0, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("startup.backlog.failed")
            return offset
        logger.debug("startup.backlog.updates", updates=updates)
        if not updates:
            if drained:
                logger.info("startup.backlog.drained", count=drained)
            return offset
        offset = updates[-1]["update_id"] + 1
        drained += len(updates)


async def poll_updates(
    cfg: TelegramBridgeConfig,
) -> AsyncIterator[TransportIncomingMessage]:
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    async for msg in poll_incoming(cfg.bot, chat_id=cfg.chat_id, offset=offset):
        yield msg


async def _handle_cancel(
    cfg: TelegramBridgeConfig,
    msg: TransportIncomingMessage,
    running_tasks: RunningTasks,
) -> None:
    chat_id = msg.chat_id
    user_msg_id = msg.message_id
    reply_id = msg.reply_to_message_id

    if reply_id is None:
        if msg.reply_to_text:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=chat_id,
                user_msg_id=user_msg_id,
                text="nothing is currently running for that message.",
            )
            return
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="reply to the progress message to cancel.",
        )
        return

    progress_ref = MessageRef(channel_id=chat_id, message_id=reply_id)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="nothing is currently running for that message.",
        )
        return

    logger.info(
        "cancel.requested",
        chat_id=chat_id,
        progress_message_id=reply_id,
    )
    running_task.cancel_requested.set()


async def _wait_for_resume(running_task: RunningTask) -> ResumeToken | None:
    if running_task.resume is not None:
        return running_task.resume
    resume: ResumeToken | None = None

    async with anyio.create_task_group() as tg:

        async def wait_resume() -> None:
            nonlocal resume
            await running_task.resume_ready.wait()
            resume = running_task.resume
            tg.cancel_scope.cancel()

        async def wait_done() -> None:
            await running_task.done.wait()
            tg.cancel_scope.cancel()

        tg.start_soon(wait_resume)
        tg.start_soon(wait_done)

    return resume


async def _send_with_resume(
    cfg: TelegramBridgeConfig,
    enqueue: Callable[[int, int, str, ResumeToken, RunContext | None], Awaitable[None]],
    running_task: RunningTask,
    chat_id: int,
    user_msg_id: int,
    text: str,
) -> None:
    resume = await _wait_for_resume(running_task)
    if resume is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="resume token not ready yet; try replying to the final message.",
            notify=False,
        )
        return
    await enqueue(chat_id, user_msg_id, text, resume, running_task.context)


async def _send_runner_unavailable(
    cfg: TelegramBridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    resume_token: ResumeToken | None,
    runner: Runner,
    reason: str,
) -> None:
    tracker = ProgressTracker(engine=runner.engine)
    tracker.set_resume(resume_token)
    state = tracker.snapshot(resume_formatter=runner.format_resume)
    message = cfg.exec_cfg.presenter.render_final(
        state,
        elapsed_s=0.0,
        status="error",
        answer=f"error:\n{reason}",
    )
    reply_to = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    await cfg.exec_cfg.transport.send(
        channel_id=chat_id,
        message=message,
        options=SendOptions(reply_to=reply_to, notify=True),
    )


async def run_main_loop(
    cfg: TelegramBridgeConfig,
    poller: Callable[
        [TelegramBridgeConfig], AsyncIterator[TransportIncomingMessage]
    ] = poll_updates,
) -> None:
    running_tasks: RunningTasks = {}

    try:
        await _set_command_menu(cfg)
        async with anyio.create_task_group() as tg:

            async def run_job(
                chat_id: int,
                user_msg_id: int,
                text: str,
                resume_token: ResumeToken | None,
                context: RunContext | None,
                reply_ref: MessageRef | None = None,
                on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
                | None = None,
                engine_override: EngineId | None = None,
            ) -> None:
                try:
                    try:
                        entry = (
                            cfg.router.entry_for_engine(engine_override)
                            if resume_token is None
                            else cfg.router.entry_for(resume_token)
                        )
                    except RunnerUnavailableError as exc:
                        await _send_plain(
                            cfg.exec_cfg.transport,
                            chat_id=chat_id,
                            user_msg_id=user_msg_id,
                            text=f"error:\n{exc}",
                        )
                        return
                    if not entry.available:
                        reason = entry.issue or "engine unavailable"
                        await _send_runner_unavailable(
                            cfg,
                            chat_id=chat_id,
                            user_msg_id=user_msg_id,
                            resume_token=resume_token,
                            runner=entry.runner,
                            reason=reason,
                        )
                        return
                    try:
                        cwd = resolve_run_cwd(context, projects=cfg.projects)
                    except WorktreeError as exc:
                        await _send_plain(
                            cfg.exec_cfg.transport,
                            chat_id=chat_id,
                            user_msg_id=user_msg_id,
                            text=f"error:\n{exc}",
                        )
                        return
                    run_base_token = set_run_base_dir(cwd)
                    try:
                        run_fields = {
                            "chat_id": chat_id,
                            "user_msg_id": user_msg_id,
                            "engine": entry.runner.engine,
                            "resume": resume_token.value if resume_token else None,
                        }
                        if context is not None:
                            run_fields["project"] = context.project
                            run_fields["branch"] = context.branch
                        if cwd is not None:
                            run_fields["cwd"] = str(cwd)
                        bind_run_context(**run_fields)
                        context_line = _format_context_line(
                            context, projects=cfg.projects
                        )
                        incoming = RunnerIncomingMessage(
                            channel_id=chat_id,
                            message_id=user_msg_id,
                            text=text,
                            reply_to=reply_ref,
                        )
                        await handle_message(
                            cfg.exec_cfg,
                            runner=entry.runner,
                            incoming=incoming,
                            resume_token=resume_token,
                            context=context,
                            context_line=context_line,
                            strip_resume_line=cfg.router.is_resume_line,
                            running_tasks=running_tasks,
                            on_thread_known=on_thread_known,
                        )
                    finally:
                        reset_run_base_dir(run_base_token)
                except Exception as exc:
                    logger.exception(
                        "handle.worker_failed",
                        error=str(exc),
                        error_type=exc.__class__.__name__,
                    )
                finally:
                    clear_context()

            async def run_thread_job(job: ThreadJob) -> None:
                await run_job(
                    job.chat_id,
                    job.user_msg_id,
                    job.text,
                    job.resume_token,
                    job.context,
                    None,
                )

            scheduler = ThreadScheduler(task_group=tg, run_job=run_thread_job)

            async for msg in poller(cfg):
                text = msg.text
                user_msg_id = msg.message_id
                chat_id = msg.chat_id
                reply_id = msg.reply_to_message_id
                reply_ref = (
                    MessageRef(channel_id=chat_id, message_id=reply_id)
                    if reply_id is not None
                    else None
                )

                if _is_cancel_command(text):
                    tg.start_soon(_handle_cancel, cfg, msg, running_tasks)
                    continue

                reply_text = msg.reply_to_text
                try:
                    resolved = _resolve_message(
                        text=text,
                        reply_text=reply_text,
                        router=cfg.router,
                        projects=cfg.projects,
                    )
                except DirectiveError as exc:
                    await _send_plain(
                        cfg.exec_cfg.transport,
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        text=f"error:\n{exc}",
                    )
                    continue

                text = resolved.prompt
                resume_token = resolved.resume_token
                engine_override = resolved.engine_override
                context = resolved.context
                if resume_token is None and reply_id is not None:
                    running_task = running_tasks.get(
                        MessageRef(channel_id=chat_id, message_id=reply_id)
                    )
                    if running_task is not None:
                        tg.start_soon(
                            _send_with_resume,
                            cfg,
                            scheduler.enqueue_resume,
                            running_task,
                            chat_id,
                            user_msg_id,
                            text,
                        )
                        continue

                if resume_token is None:
                    tg.start_soon(
                        run_job,
                        chat_id,
                        user_msg_id,
                        text,
                        None,
                        context,
                        reply_ref,
                        scheduler.note_thread_known,
                        engine_override,
                    )
                else:
                    await scheduler.enqueue_resume(
                        chat_id,
                        user_msg_id,
                        text,
                        resume_token,
                        context,
                    )
    finally:
        await cfg.exec_cfg.transport.close()
