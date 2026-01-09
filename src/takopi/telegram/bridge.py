from __future__ import annotations

import os
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import anyio

from ..commands import (
    CommandContext,
    CommandExecutor,
    RunMode,
    RunRequest,
    RunResult,
    get_command,
    list_command_ids,
)
from ..context import RunContext
from ..config import ConfigError
from ..config_watch import ConfigReload, watch_config as watch_config_changes
from ..directives import DirectiveError
from ..ids import RESERVED_COMMAND_IDS, is_valid_id
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
from ..router import RunnerUnavailableError
from ..runner import Runner
from ..scheduler import ThreadJob, ThreadScheduler
from ..transport import MessageRef, RenderedMessage, SendOptions, Transport
from ..plugins import COMMAND_GROUP, list_entrypoints
from ..utils.paths import reset_run_base_dir, set_run_base_dir
from ..transport_runtime import TransportRuntime
from .client import BotClient, poll_incoming
from .types import TelegramIncomingMessage
from .render import prepare_telegram
from .transcribe import transcribe_audio

logger = get_logger(__name__)

_MAX_BOT_COMMANDS = 100
_OPENAI_AUDIO_MAX_BYTES = 25 * 1024 * 1024
_OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
_OPENAI_TRANSCRIPTION_CHUNKING = "auto"


def _is_cancel_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")


def _parse_slash_command(text: str) -> tuple[str | None, str]:
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return None, text
    lines = stripped.splitlines()
    if not lines:
        return None, text
    first_line = lines[0]
    token, _, rest = first_line.partition(" ")
    command = token[1:]
    if not command:
        return None, text
    if "@" in command:
        command = command.split("@", 1)[0]
    args_text = rest
    if len(lines) > 1:
        tail = "\n".join(lines[1:])
        args_text = f"{args_text}\n{tail}" if args_text else tail
    return command.lower(), args_text


def _build_bot_commands(runtime: TransportRuntime) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for engine_id in runtime.available_engine_ids():
        cmd = engine_id.lower()
        if cmd in seen:
            continue
        commands.append({"command": cmd, "description": f"use agent: {cmd}"})
        seen.add(cmd)
    for alias in runtime.project_aliases():
        cmd = alias.lower()
        if cmd in seen:
            continue
        if not is_valid_id(cmd):
            logger.debug(
                "startup.command_menu.skip_project",
                alias=alias,
            )
            continue
        commands.append({"command": cmd, "description": f"work on: {cmd}"})
        seen.add(cmd)
    allowlist = runtime.allowlist
    for ep in list_entrypoints(
        COMMAND_GROUP,
        allowlist=allowlist,
        reserved_ids=RESERVED_COMMAND_IDS,
    ):
        try:
            backend = get_command(ep.name, allowlist=allowlist)
        except ConfigError as exc:
            logger.info(
                "startup.command_menu.skip_command",
                command=ep.name,
                error=str(exc),
            )
            continue
        cmd = backend.id.lower()
        if cmd in seen:
            continue
        if not is_valid_id(cmd):
            logger.debug(
                "startup.command_menu.skip_command_id",
                command=cmd,
            )
            continue
        description = backend.description or f"command: {cmd}"
        commands.append({"command": cmd, "description": description})
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


def _reserved_commands(runtime: TransportRuntime) -> set[str]:
    return {
        *{engine.lower() for engine in runtime.engine_ids},
        *{alias.lower() for alias in runtime.project_aliases()},
        *RESERVED_COMMAND_IDS,
    }


@dataclass(slots=True)
class RuntimeCommandCache:
    command_ids: set[str]
    reserved_commands: set[str]

    @classmethod
    def from_runtime(cls, runtime: TransportRuntime) -> "RuntimeCommandCache":
        allowlist = runtime.allowlist
        return cls(
            command_ids={
                command_id.lower()
                for command_id in list_command_ids(allowlist=allowlist)
            },
            reserved_commands=_reserved_commands(runtime),
        )

    def refresh(self, runtime: TransportRuntime) -> None:
        allowlist = runtime.allowlist
        self.command_ids = {
            command_id.lower() for command_id in list_command_ids(allowlist=allowlist)
        }
        self.reserved_commands = _reserved_commands(runtime)


def _diff_keys(old: dict[str, object], new: dict[str, object]) -> list[str]:
    keys = set(old) | set(new)
    return sorted(key for key in keys if old.get(key) != new.get(key))


async def _set_command_menu(cfg: TelegramBridgeConfig) -> None:
    commands = _build_bot_commands(cfg.runtime)
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


@dataclass(frozen=True)
class TelegramVoiceTranscriptionConfig:
    enabled: bool = False


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
    runtime: TransportRuntime
    chat_id: int
    startup_msg: str
    exec_cfg: ExecBridgeConfig
    voice_transcription: TelegramVoiceTranscriptionConfig | None = None
    chat_ids: tuple[int, ...] | None = None


def _allowed_chat_ids(cfg: TelegramBridgeConfig) -> set[int]:
    allowed = set(cfg.chat_ids or ())
    allowed.add(cfg.chat_id)
    allowed.update(cfg.runtime.project_chat_ids())
    return allowed


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
) -> AsyncIterator[TelegramIncomingMessage]:
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    async for msg in poll_incoming(
        cfg.bot,
        chat_ids=lambda: _allowed_chat_ids(cfg),
        offset=offset,
    ):
        yield msg


def _resolve_openai_api_key(
    cfg: TelegramVoiceTranscriptionConfig,
) -> str | None:
    env_key = os.environ.get("OPENAI_API_KEY")
    if isinstance(env_key, str):
        env_key = env_key.strip()
        if env_key:
            return env_key
    return None


def _normalize_voice_filename(file_path: str | None, mime_type: str | None) -> str:
    name = Path(file_path).name if file_path else ""
    if not name:
        if mime_type == "audio/ogg":
            return "voice.ogg"
        return "voice.dat"
    if name.endswith(".oga"):
        return f"{name[:-4]}.ogg"
    return name


async def _transcribe_voice(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
) -> str | None:
    voice = msg.voice
    if voice is None:
        return msg.text
    settings = cfg.voice_transcription
    if settings is None or not settings.enabled:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice transcription is disabled.",
        )
        return None
    api_key = _resolve_openai_api_key(settings)
    if not api_key:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice transcription requires OPENAI_API_KEY.",
        )
        return None
    if voice.file_size is not None and voice.file_size > _OPENAI_AUDIO_MAX_BYTES:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice message is too large to transcribe.",
        )
        return None
    file_info = await cfg.bot.get_file(voice.file_id)
    if not isinstance(file_info, dict):
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to fetch voice file.",
        )
        return None
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to fetch voice file.",
        )
        return None
    audio_bytes = await cfg.bot.download_file(file_path)
    if not audio_bytes:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to download voice message.",
        )
        return None
    if len(audio_bytes) > _OPENAI_AUDIO_MAX_BYTES:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice message is too large to transcribe.",
        )
        return None
    filename = _normalize_voice_filename(file_path, voice.mime_type)
    transcript = await transcribe_audio(
        audio_bytes,
        filename=filename,
        api_key=api_key,
        model=_OPENAI_TRANSCRIPTION_MODEL,
        chunking_strategy=_OPENAI_TRANSCRIPTION_CHUNKING,
        mime_type=voice.mime_type,
    )
    if transcript is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice transcription failed.",
        )
        return None
    transcript = transcript.strip()
    if not transcript:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice transcription returned empty text.",
        )
        return None
    return transcript


async def _handle_cancel(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
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
    exec_cfg: ExecBridgeConfig,
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
    message = exec_cfg.presenter.render_final(
        state,
        elapsed_s=0.0,
        status="error",
        answer=f"error:\n{reason}",
    )
    reply_to = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    await exec_cfg.transport.send(
        channel_id=chat_id,
        message=message,
        options=SendOptions(reply_to=reply_to, notify=True),
    )


async def _run_engine(
    *,
    exec_cfg: ExecBridgeConfig,
    runtime: TransportRuntime,
    running_tasks: RunningTasks | None,
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
            entry = runtime.resolve_runner(
                resume_token=resume_token,
                engine_override=engine_override,
            )
        except RunnerUnavailableError as exc:
            await _send_plain(
                exec_cfg.transport,
                chat_id=chat_id,
                user_msg_id=user_msg_id,
                text=f"error:\n{exc}",
            )
            return
        if not entry.available:
            reason = entry.issue or "engine unavailable"
            await _send_runner_unavailable(
                exec_cfg,
                chat_id=chat_id,
                user_msg_id=user_msg_id,
                resume_token=resume_token,
                runner=entry.runner,
                reason=reason,
            )
            return
        try:
            cwd = runtime.resolve_run_cwd(context)
        except ConfigError as exc:
            await _send_plain(
                exec_cfg.transport,
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
            context_line = runtime.format_context_line(context)
            incoming = RunnerIncomingMessage(
                channel_id=chat_id,
                message_id=user_msg_id,
                text=text,
                reply_to=reply_ref,
            )
            await handle_message(
                exec_cfg,
                runner=entry.runner,
                incoming=incoming,
                resume_token=resume_token,
                context=context,
                context_line=context_line,
                strip_resume_line=runtime.is_resume_line,
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


def _split_command_args(text: str) -> tuple[str, ...]:
    if not text.strip():
        return ()
    try:
        return tuple(shlex.split(text))
    except ValueError:
        return tuple(text.split())


class _CaptureTransport:
    def __init__(self) -> None:
        self._next_id = 1
        self.last_message: RenderedMessage | None = None

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        _ = options
        ref = MessageRef(channel_id=channel_id, message_id=self._next_id)
        self._next_id += 1
        self.last_message = message
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        _ = ref, wait
        self.last_message = message
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        _ = ref
        return True

    async def close(self) -> None:
        return None


class _TelegramCommandExecutor(CommandExecutor):
    def __init__(
        self,
        *,
        exec_cfg: ExecBridgeConfig,
        runtime: TransportRuntime,
        running_tasks: RunningTasks,
        scheduler: ThreadScheduler,
        chat_id: int,
        user_msg_id: int,
    ) -> None:
        self._exec_cfg = exec_cfg
        self._runtime = runtime
        self._running_tasks = running_tasks
        self._scheduler = scheduler
        self._chat_id = chat_id
        self._user_msg_id = user_msg_id
        self._reply_ref = MessageRef(channel_id=chat_id, message_id=user_msg_id)

    def _apply_default_context(self, request: RunRequest) -> RunRequest:
        if request.context is not None:
            return request
        context = self._runtime.default_context_for_chat(self._chat_id)
        if context is None:
            return request
        return RunRequest(
            prompt=request.prompt,
            engine=request.engine,
            context=context,
        )

    async def send(
        self,
        message: RenderedMessage | str,
        *,
        reply_to: MessageRef | None = None,
        notify: bool = True,
    ) -> MessageRef | None:
        rendered = (
            message
            if isinstance(message, RenderedMessage)
            else RenderedMessage(text=message)
        )
        reply_ref = self._reply_ref if reply_to is None else reply_to
        return await self._exec_cfg.transport.send(
            channel_id=self._chat_id,
            message=rendered,
            options=SendOptions(reply_to=reply_ref, notify=notify),
        )

    async def run_one(
        self, request: RunRequest, *, mode: RunMode = "emit"
    ) -> RunResult:
        request = self._apply_default_context(request)
        engine = self._runtime.resolve_engine(
            engine_override=request.engine,
            context=request.context,
        )
        if mode == "capture":
            capture = _CaptureTransport()
            exec_cfg = ExecBridgeConfig(
                transport=capture,
                presenter=self._exec_cfg.presenter,
                final_notify=False,
            )
            await _run_engine(
                exec_cfg=exec_cfg,
                runtime=self._runtime,
                running_tasks={},
                chat_id=self._chat_id,
                user_msg_id=self._user_msg_id,
                text=request.prompt,
                resume_token=None,
                context=request.context,
                reply_ref=self._reply_ref,
                on_thread_known=None,
                engine_override=engine,
            )
            return RunResult(engine=engine, message=capture.last_message)
        await _run_engine(
            exec_cfg=self._exec_cfg,
            runtime=self._runtime,
            running_tasks=self._running_tasks,
            chat_id=self._chat_id,
            user_msg_id=self._user_msg_id,
            text=request.prompt,
            resume_token=None,
            context=request.context,
            reply_ref=self._reply_ref,
            on_thread_known=self._scheduler.note_thread_known,
            engine_override=engine,
        )
        return RunResult(engine=engine, message=None)

    async def run_many(
        self,
        requests: Sequence[RunRequest],
        *,
        mode: RunMode = "emit",
        parallel: bool = False,
    ) -> list[RunResult]:
        if not parallel:
            return [await self.run_one(request, mode=mode) for request in requests]
        results: list[RunResult | None] = [None] * len(requests)

        async with anyio.create_task_group() as tg:

            async def run_idx(idx: int, request: RunRequest) -> None:
                results[idx] = await self.run_one(request, mode=mode)

            for idx, request in enumerate(requests):
                tg.start_soon(run_idx, idx, request)

        return [result for result in results if result is not None]


async def _dispatch_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    text: str,
    command_id: str,
    args_text: str,
    running_tasks: RunningTasks,
    scheduler: ThreadScheduler,
) -> None:
    allowlist = cfg.runtime.allowlist
    chat_id = msg.chat_id
    user_msg_id = msg.message_id
    reply_ref = (
        MessageRef(channel_id=chat_id, message_id=msg.reply_to_message_id)
        if msg.reply_to_message_id is not None
        else None
    )
    executor = _TelegramCommandExecutor(
        exec_cfg=cfg.exec_cfg,
        runtime=cfg.runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
    )
    message_ref = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    try:
        backend = get_command(command_id, allowlist=allowlist, required=False)
    except ConfigError as exc:
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    if backend is None:
        return
    try:
        plugin_config = cfg.runtime.plugin_config(command_id)
    except ConfigError as exc:
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    ctx = CommandContext(
        command=command_id,
        text=text,
        args_text=args_text,
        args=_split_command_args(args_text),
        message=message_ref,
        reply_to=reply_ref,
        reply_text=msg.reply_to_text,
        config_path=cfg.runtime.config_path,
        plugin_config=plugin_config,
        runtime=cfg.runtime,
        executor=executor,
    )
    try:
        result = await backend.handle(ctx)
    except Exception as exc:
        logger.exception(
            "command.failed",
            command=command_id,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    if result is not None:
        reply_to = message_ref if result.reply_to is None else result.reply_to
        await executor.send(result.text, reply_to=reply_to, notify=result.notify)
    return None


async def run_main_loop(
    cfg: TelegramBridgeConfig,
    poller: Callable[[TelegramBridgeConfig], AsyncIterator[TelegramIncomingMessage]] = (
        poll_updates
    ),
    *,
    watch_config: bool | None = None,
    default_engine_override: str | None = None,
    transport_id: str | None = None,
    transport_config: dict[str, object] | None = None,
) -> None:
    running_tasks: RunningTasks = {}
    command_cache = RuntimeCommandCache.from_runtime(cfg.runtime)
    transport_snapshot = (
        dict(transport_config) if transport_config is not None else None
    )

    try:
        await _set_command_menu(cfg)
        async with anyio.create_task_group() as tg:
            config_path = cfg.runtime.config_path
            watch_enabled = bool(watch_config) and config_path is not None

            async def handle_reload(reload: ConfigReload) -> None:
                nonlocal transport_snapshot, transport_id
                command_cache.refresh(cfg.runtime)
                await _set_command_menu(cfg)
                if transport_snapshot is not None:
                    new_snapshot = reload.settings.transports.telegram.model_dump()
                    changed = _diff_keys(transport_snapshot, new_snapshot)
                    if changed:
                        logger.warning(
                            "config.reload.transport_config_changed",
                            transport="telegram",
                            keys=changed,
                            restart_required=True,
                        )
                        transport_snapshot = new_snapshot
                if (
                    transport_id is not None
                    and reload.settings.transport != transport_id
                ):
                    logger.warning(
                        "config.reload.transport_changed",
                        old=transport_id,
                        new=reload.settings.transport,
                        restart_required=True,
                    )
                    transport_id = reload.settings.transport

            if watch_enabled and config_path is not None:

                async def run_config_watch() -> None:
                    await watch_config_changes(
                        config_path=config_path,
                        runtime=cfg.runtime,
                        default_engine_override=default_engine_override,
                        on_reload=handle_reload,
                    )

                tg.start_soon(run_config_watch)

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
                await _run_engine(
                    exec_cfg=cfg.exec_cfg,
                    runtime=cfg.runtime,
                    running_tasks=running_tasks,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    text=text,
                    resume_token=resume_token,
                    context=context,
                    reply_ref=reply_ref,
                    on_thread_known=on_thread_known,
                    engine_override=engine_override,
                )

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
                if msg.voice is not None:
                    text = await _transcribe_voice(cfg, msg)
                    if text is None:
                        continue
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

                command_id, args_text = _parse_slash_command(text)
                if (
                    command_id is not None
                    and command_id not in command_cache.reserved_commands
                ):
                    if command_id not in command_cache.command_ids:
                        command_cache.refresh(cfg.runtime)
                    if command_id in command_cache.command_ids:
                        tg.start_soon(
                            _dispatch_command,
                            cfg,
                            msg,
                            text,
                            command_id,
                            args_text,
                            running_tasks,
                            scheduler,
                        )
                        continue

                reply_text = msg.reply_to_text
                try:
                    resolved = cfg.runtime.resolve_message(
                        text=text,
                        reply_text=reply_text,
                        chat_id=chat_id,
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
