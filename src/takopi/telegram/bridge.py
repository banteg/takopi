from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
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
from ..transport_runtime import ResolvedMessage, TransportRuntime
from .client import BotClient, poll_incoming
from .files import (
    default_upload_name,
    default_upload_path,
    deny_reason,
    file_get_usage,
    file_put_usage,
    format_bytes,
    normalize_relative_path,
    parse_file_command,
    parse_file_prompt,
    resolve_path_within_root,
    split_command_args,
    write_bytes_atomic,
    ZipTooLargeError,
    zip_directory,
)
from .types import (
    TelegramCallbackQuery,
    TelegramDocument,
    TelegramIncomingMessage,
    TelegramIncomingUpdate,
)
from .render import prepare_telegram
from .topic_state import TopicStateStore, TopicThreadSnapshot, resolve_state_path
from .transcribe import transcribe_audio

logger = get_logger(__name__)

_MAX_BOT_COMMANDS = 100
_OPENAI_AUDIO_MAX_BYTES = 25 * 1024 * 1024
_OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
_OPENAI_TRANSCRIPTION_CHUNKING = "auto"
_MEDIA_GROUP_DEBOUNCE_S = 1.0
CANCEL_CALLBACK_DATA = "takopi:cancel"
CANCEL_MARKUP = {
    "inline_keyboard": [[{"text": "cancel", "callback_data": CANCEL_CALLBACK_DATA}]]
}
CLEAR_MARKUP = {"inline_keyboard": []}


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


_TOPICS_COMMANDS = {"ctx", "new", "topic"}


def _resolve_topics_scope(cfg: TelegramBridgeConfig) -> tuple[str, frozenset[int]]:
    scope = cfg.topics.scope
    project_ids = set(cfg.runtime.project_chat_ids())
    if scope == "auto":
        scope = "projects" if project_ids else "main"
    if scope == "main":
        return scope, frozenset({cfg.chat_id})
    if scope == "projects":
        return scope, frozenset(project_ids)
    if scope == "all":
        return scope, frozenset({cfg.chat_id, *project_ids})
    raise ValueError(f"Invalid topics.scope: {cfg.topics.scope!r}")


def _topics_scope_label(cfg: TelegramBridgeConfig) -> str:
    resolved, _ = _resolve_topics_scope(cfg)
    if cfg.topics.scope == "auto":
        return f"auto ({resolved})"
    return resolved


def _topics_chat_project(cfg: TelegramBridgeConfig, chat_id: int) -> str | None:
    context = cfg.runtime.default_context_for_chat(chat_id)
    return context.project if context is not None else None


def _topics_chat_allowed(cfg: TelegramBridgeConfig, chat_id: int) -> bool:
    if not cfg.topics.enabled:
        return False
    _, scope_chat_ids = _resolve_topics_scope(cfg)
    return chat_id in scope_chat_ids


def _topics_command_error(cfg: TelegramBridgeConfig, chat_id: int) -> str | None:
    if _topics_chat_allowed(cfg, chat_id):
        return None
    resolved, _ = _resolve_topics_scope(cfg)
    if resolved == "main":
        if cfg.topics.scope == "auto":
            return (
                "topics commands are only available in the main chat (auto scope). "
                'to use topics in project chats, set `topics.scope = "projects"`.'
            )
        return "topics commands are only available in the main chat."
    if resolved == "projects":
        if cfg.topics.scope == "auto":
            return (
                "topics commands are only available in project chats (auto scope). "
                'to use topics in the main chat, set `topics.scope = "main"`.'
            )
        return "topics commands are only available in project chats."
    return "topics commands are only available in the main or project chats."


def _merge_topic_context(
    *, chat_project: str | None, bound: RunContext | None
) -> RunContext | None:
    if chat_project is None:
        return bound
    if bound is None:
        return RunContext(project=chat_project, branch=None)
    if bound.project is None:
        return RunContext(project=chat_project, branch=bound.branch)
    return bound


def _topic_key(
    msg: TelegramIncomingMessage, cfg: TelegramBridgeConfig
) -> tuple[int, int] | None:
    if not cfg.topics.enabled:
        return None
    if not _topics_chat_allowed(cfg, msg.chat_id):
        return None
    if msg.thread_id is None:
        return None
    return (msg.chat_id, msg.thread_id)


def _format_context(runtime: TransportRuntime, context: RunContext | None) -> str:
    if context is None or context.project is None:
        return "none"
    project = runtime.project_alias_for_key(context.project)
    if context.branch:
        return f"{project} @{context.branch}"
    return project


def _usage_ctx_set(*, chat_project: str | None) -> str:
    if chat_project is not None:
        return "usage: `/ctx set [@branch]`"
    return "usage: `/ctx set <project> [@branch]`"


def _usage_topic(*, chat_project: str | None) -> str:
    if chat_project is not None:
        return "usage: `/topic @branch`"
    return "usage: `/topic <project> @branch`"


def _parse_project_branch_args(
    args_text: str,
    *,
    runtime: TransportRuntime,
    cfg: TelegramBridgeConfig,
    require_branch: bool,
    chat_project: str | None,
) -> tuple[RunContext | None, str | None]:
    tokens = split_command_args(args_text)
    if not tokens:
        return (
            None,
            _usage_topic(chat_project=chat_project)
            if require_branch
            else _usage_ctx_set(chat_project=chat_project),
        )
    if len(tokens) > 2:
        return None, "too many arguments"
    project_token: str | None = None
    branch: str | None = None
    first = tokens[0]
    if first.startswith("@"):
        branch = first[1:] or None
    else:
        project_token = first
        if len(tokens) == 2:
            second = tokens[1]
            if not second.startswith("@"):
                return None, "branch must be prefixed with @"
            branch = second[1:] or None

    project_key: str | None = None
    if chat_project is not None:
        if project_token is None:
            project_key = chat_project
        else:
            normalized = runtime.normalize_project_key(project_token)
            if normalized is None:
                return None, f"unknown project {project_token!r}"
            if normalized != chat_project:
                expected = runtime.project_alias_for_key(chat_project)
                return None, (f"project mismatch for this chat; expected {expected!r}.")
            project_key = normalized
    else:
        if project_token is None:
            return None, "project is required"
        project_key = runtime.normalize_project_key(project_token)
        if project_key is None:
            return None, f"unknown project {project_token!r}"

    if require_branch and not branch:
        return None, "branch is required"

    return RunContext(project=project_key, branch=branch), None


def _format_ctx_status(
    *,
    cfg: TelegramBridgeConfig,
    runtime: TransportRuntime,
    bound: RunContext | None,
    resolved: RunContext | None,
    context_source: str,
    snapshot: TopicThreadSnapshot | None,
    chat_project: str | None,
) -> str:
    lines = [
        f"topics: enabled (scope={_topics_scope_label(cfg)})",
        f"bound ctx: {_format_context(runtime, bound)}",
        f"resolved ctx: {_format_context(runtime, resolved)} (source: {context_source})",
    ]
    if chat_project is None and bound is None:
        topic_usage = (
            _usage_topic(chat_project=chat_project).removeprefix("usage: ").strip()
        )
        ctx_usage = (
            _usage_ctx_set(chat_project=chat_project).removeprefix("usage: ").strip()
        )
        lines.append(f"note: unbound topic — bind with {topic_usage} or {ctx_usage}")
    sessions = None
    if snapshot is not None and snapshot.sessions:
        sessions = ", ".join(sorted(snapshot.sessions))
    lines.append(f"sessions: {sessions or 'none'}")
    return "\n".join(lines)


def _build_bot_commands(
    runtime: TransportRuntime, *, include_file: bool = True
) -> list[dict[str, str]]:
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
    if include_file and "file" not in seen:
        commands.append({"command": "file", "description": "upload or fetch files"})
        seen.add("file")
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
    commands = _build_bot_commands(cfg.runtime, include_file=cfg.files.enabled)
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
        reply_markup = CLEAR_MARKUP if _is_cancelled_label(label) else CANCEL_MARKUP
        return RenderedMessage(
            text=text,
            extra={"entities": entities, "reply_markup": reply_markup},
        )

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
        return RenderedMessage(
            text=text,
            extra={"entities": entities, "reply_markup": CLEAR_MARKUP},
        )


def _is_cancelled_label(label: str) -> bool:
    stripped = label.strip()
    if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
        stripped = stripped[1:-1]
    return stripped.lower() == "cancelled"


@dataclass(frozen=True)
class TelegramVoiceTranscriptionConfig:
    enabled: bool = False


@dataclass(frozen=True)
class TelegramFilesConfig:
    enabled: bool = False
    auto_put: bool = True
    uploads_dir: str = "incoming"
    max_upload_bytes: int = 20 * 1024 * 1024
    max_download_bytes: int = 50 * 1024 * 1024
    allowed_user_ids: frozenset[int] = frozenset()
    deny_globs: tuple[str, ...] = (
        ".git/**",
        ".env",
        ".envrc",
        "**/*.pem",
        "**/.ssh/**",
    )


@dataclass(frozen=True)
class TelegramTopicsConfig:
    enabled: bool = False
    scope: str = "auto"


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
        message_thread_id: int | None = None
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
            if options.thread_id is not None:
                message_thread_id = _as_int(
                    options.thread_id, label="message_thread_id"
                )
        entities = message.extra.get("entities")
        parse_mode = message.extra.get("parse_mode")
        reply_markup = message.extra.get("reply_markup")
        sent = await self._bot.send_message(
            chat_id=chat_id,
            text=message.text,
            reply_to_message_id=reply_to_message_id,
            disable_notification=disable_notification,
            message_thread_id=message_thread_id,
            entities=entities,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
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
        reply_markup = message.extra.get("reply_markup")
        edited = await self._bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message.text,
            entities=entities,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
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
    files: TelegramFilesConfig = TelegramFilesConfig()
    chat_ids: tuple[int, ...] | None = None
    topics: TelegramTopicsConfig = TelegramTopicsConfig()


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
    thread_id: int | None = None,
) -> None:
    reply_to = MessageRef(channel_id=chat_id, message_id=user_msg_id)
    rendered_text, entities = prepare_telegram(MarkdownParts(header=text))
    await transport.send(
        channel_id=chat_id,
        message=RenderedMessage(text=rendered_text, extra={"entities": entities}),
        options=SendOptions(reply_to=reply_to, notify=notify, thread_id=thread_id),
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


async def _validate_topics_setup(cfg: TelegramBridgeConfig) -> None:
    if not cfg.topics.enabled:
        return
    me = await cfg.bot.get_me()
    bot_id = me.get("id") if isinstance(me, dict) else None
    if not isinstance(bot_id, int):
        raise ConfigError("failed to fetch bot id for topics validation.")
    scope, chat_ids = _resolve_topics_scope(cfg)
    if scope == "projects" and not chat_ids:
        raise ConfigError(
            "topics enabled but no project chats are configured; "
            'set projects.<alias>.chat_id for forum chats or use scope="main".'
        )

    for chat_id in chat_ids:
        chat = await cfg.bot.get_chat(chat_id)
        if not isinstance(chat, dict):
            raise ConfigError(
                f"failed to fetch chat info for topics validation ({chat_id})."
            )
        chat_type = chat.get("type")
        is_forum = chat.get("is_forum")
        if chat_type != "supergroup":
            raise ConfigError(
                "topics enabled but chat is not a supergroup "
                f"(chat_id={chat_id}); convert the group and enable topics."
            )
        if is_forum is not True:
            raise ConfigError(
                "topics enabled but chat does not have topics enabled "
                f"(chat_id={chat_id}); turn on topics in group settings."
            )
        member = await cfg.bot.get_chat_member(chat_id, bot_id)
        if not isinstance(member, dict):
            raise ConfigError(
                "failed to fetch bot permissions "
                f"(chat_id={chat_id}); promote the bot to admin with manage topics."
            )
        status = member.get("status")
        if status == "creator":
            continue
        if status != "administrator":
            raise ConfigError(
                "topics enabled but bot is not an admin "
                f"(chat_id={chat_id}); promote it and grant manage topics."
            )
        if member.get("can_manage_topics") is not True:
            raise ConfigError(
                "topics enabled but bot lacks manage topics permission "
                f"(chat_id={chat_id}); grant can_manage_topics."
            )


async def _drain_backlog(cfg: TelegramBridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        updates = await cfg.bot.get_updates(
            offset=offset,
            timeout_s=0,
            allowed_updates=["message", "callback_query"],
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
) -> AsyncIterator[TelegramIncomingUpdate]:
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
            thread_id=msg.thread_id,
        )
        return None
    api_key = _resolve_openai_api_key(settings)
    if not api_key:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice transcription requires OPENAI_API_KEY.",
            thread_id=msg.thread_id,
        )
        return None
    if voice.file_size is not None and voice.file_size > _OPENAI_AUDIO_MAX_BYTES:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice message is too large to transcribe.",
            thread_id=msg.thread_id,
        )
        return None
    file_info = await cfg.bot.get_file(voice.file_id)
    if not isinstance(file_info, dict):
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to fetch voice file.",
            thread_id=msg.thread_id,
        )
        return None
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to fetch voice file.",
            thread_id=msg.thread_id,
        )
        return None
    audio_bytes = await cfg.bot.download_file(file_path)
    if not audio_bytes:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to download voice message.",
            thread_id=msg.thread_id,
        )
        return None
    if len(audio_bytes) > _OPENAI_AUDIO_MAX_BYTES:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice message is too large to transcribe.",
            thread_id=msg.thread_id,
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
            thread_id=msg.thread_id,
        )
        return None
    transcript = transcript.strip()
    if not transcript:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="voice transcription returned empty text.",
            thread_id=msg.thread_id,
        )
        return None
    return transcript


@dataclass(slots=True)
class _FilePutPlan:
    resolved: ResolvedMessage
    run_root: Path
    path_value: str | None
    force: bool


@dataclass(slots=True)
class _FilePutResult:
    name: str
    rel_path: Path | None
    size: int | None
    error: str | None


@dataclass(slots=True)
class _MediaGroupState:
    messages: list[TelegramIncomingMessage]
    token: int = 0


async def _check_file_permissions(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> bool:
    sender_id = msg.sender_id
    if sender_id is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="cannot verify sender for file transfer.",
            thread_id=msg.thread_id,
        )
        return False
    if cfg.files.allowed_user_ids:
        if sender_id not in cfg.files.allowed_user_ids:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text="file transfer is not allowed for this user.",
                thread_id=msg.thread_id,
            )
            return False
        return True
    is_private = msg.chat_type == "private"
    if msg.chat_type is None:
        is_private = msg.chat_id > 0
    if is_private:
        return True
    member = await cfg.bot.get_chat_member(msg.chat_id, sender_id)
    if not isinstance(member, dict):
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to verify file transfer permissions.",
            thread_id=msg.thread_id,
        )
        return False
    status = member.get("status")
    if status in {"creator", "administrator"}:
        return True
    await _send_plain(
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        text="file transfer is restricted to group admins.",
        thread_id=msg.thread_id,
    )
    return False


async def _prepare_file_put_plan(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> _FilePutPlan | None:
    if not await _check_file_permissions(cfg, msg):
        return None
    try:
        resolved = cfg.runtime.resolve_message(
            text=args_text,
            reply_text=msg.reply_to_text,
            ambient_context=ambient_context,
            chat_id=msg.chat_id,
        )
    except DirectiveError as exc:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=f"error:\n{exc}",
            thread_id=msg.thread_id,
        )
        return None
    topic_key = _topic_key(msg, cfg) if topic_store is not None else None
    await _maybe_update_topic_context(
        cfg=cfg,
        topic_store=topic_store,
        topic_key=topic_key,
        context=resolved.context,
        context_source=resolved.context_source,
    )
    if resolved.context is None or resolved.context.project is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="no project context available for file upload.",
            thread_id=msg.thread_id,
        )
        return None
    try:
        run_root = cfg.runtime.resolve_run_cwd(resolved.context)
    except ConfigError as exc:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=f"error:\n{exc}",
            thread_id=msg.thread_id,
        )
        return None
    if run_root is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="no project context available for file upload.",
            thread_id=msg.thread_id,
        )
        return None
    path_value, force, error = parse_file_prompt(resolved.prompt, allow_empty=True)
    if error is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=error,
            thread_id=msg.thread_id,
        )
        return None
    return _FilePutPlan(
        resolved=resolved,
        run_root=run_root,
        path_value=path_value,
        force=force,
    )


async def _save_document_payload(
    cfg: TelegramBridgeConfig,
    *,
    document: TelegramDocument,
    run_root: Path,
    rel_path: Path | None,
    base_dir: Path | None,
    force: bool,
) -> _FilePutResult:
    name = default_upload_name(document.file_name, None)
    if (
        document.file_size is not None
        and document.file_size > cfg.files.max_upload_bytes
    ):
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="file is too large to upload.",
        )
    file_info = await cfg.bot.get_file(document.file_id)
    if not isinstance(file_info, dict):
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="failed to fetch file metadata.",
        )
    file_path = file_info.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="failed to fetch file metadata.",
        )
    name = default_upload_name(document.file_name, file_path)
    resolved_path = rel_path
    if resolved_path is None:
        if base_dir is None:
            resolved_path = default_upload_path(
                cfg.files.uploads_dir, document.file_name, file_path
            )
        else:
            resolved_path = base_dir / name
    deny_rule = deny_reason(resolved_path, cfg.files.deny_globs)
    if deny_rule is not None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error=f"path denied by rule: {deny_rule}",
        )
    target = resolve_path_within_root(run_root, resolved_path)
    if target is None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="upload path escapes the repo root.",
        )
    if target.exists():
        if target.is_dir():
            return _FilePutResult(
                name=name,
                rel_path=None,
                size=None,
                error="upload target is a directory.",
            )
        if not force:
            return _FilePutResult(
                name=name,
                rel_path=None,
                size=None,
                error="file already exists; use --force to overwrite.",
            )
    payload = await cfg.bot.download_file(file_path)
    if payload is None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="failed to download file.",
        )
    if len(payload) > cfg.files.max_upload_bytes:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="file is too large to upload.",
        )
    try:
        write_bytes_atomic(target, payload)
    except OSError as exc:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error=f"failed to write file: {exc}",
        )
    return _FilePutResult(
        name=name,
        rel_path=resolved_path,
        size=len(payload),
        error=None,
    )


async def _maybe_update_topic_context(
    *,
    cfg: TelegramBridgeConfig,
    topic_store: TopicStateStore | None,
    topic_key: tuple[int, int] | None,
    context: RunContext | None,
    context_source: str,
) -> None:
    if (
        topic_store is None
        or topic_key is None
        or context is None
        or context_source != "directives"
    ):
        return
    await topic_store.set_context(topic_key[0], topic_key[1], context)
    await _maybe_rename_topic(
        cfg,
        topic_store,
        chat_id=topic_key[0],
        thread_id=topic_key[1],
        context=context,
    )


async def _handle_file_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    command, rest, error = parse_file_command(args_text)
    if error is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=error,
            thread_id=msg.thread_id,
        )
        return
    if command == "put":
        await _handle_file_put(cfg, msg, rest, ambient_context, topic_store)
    else:
        await _handle_file_get(cfg, msg, rest, ambient_context, topic_store)


async def _handle_file_put_default(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    await _handle_file_put(cfg, msg, "", ambient_context, topic_store)


async def _handle_file_put(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    document = msg.document
    if document is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=file_put_usage(),
            thread_id=msg.thread_id,
        )
        return
    plan = await _prepare_file_put_plan(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if plan is None:
        return
    rel_path: Path | None = None
    base_dir: Path | None = None
    if plan.path_value:
        if plan.path_value.endswith("/"):
            base_dir = normalize_relative_path(plan.path_value)
            if base_dir is None:
                await _send_plain(
                    cfg.exec_cfg.transport,
                    chat_id=msg.chat_id,
                    user_msg_id=msg.message_id,
                    text="invalid upload path.",
                    thread_id=msg.thread_id,
                )
                return
            deny_rule = deny_reason(base_dir, cfg.files.deny_globs)
            if deny_rule is not None:
                await _send_plain(
                    cfg.exec_cfg.transport,
                    chat_id=msg.chat_id,
                    user_msg_id=msg.message_id,
                    text=f"path denied by rule: {deny_rule}",
                    thread_id=msg.thread_id,
                )
                return
            base_target = resolve_path_within_root(plan.run_root, base_dir)
            if base_target is None:
                await _send_plain(
                    cfg.exec_cfg.transport,
                    chat_id=msg.chat_id,
                    user_msg_id=msg.message_id,
                    text="upload path escapes the repo root.",
                    thread_id=msg.thread_id,
                )
                return
            if base_target.exists() and not base_target.is_dir():
                await _send_plain(
                    cfg.exec_cfg.transport,
                    chat_id=msg.chat_id,
                    user_msg_id=msg.message_id,
                    text="upload path is a file.",
                    thread_id=msg.thread_id,
                )
                return
        else:
            rel_path = normalize_relative_path(plan.path_value)
            if rel_path is None:
                await _send_plain(
                    cfg.exec_cfg.transport,
                    chat_id=msg.chat_id,
                    user_msg_id=msg.message_id,
                    text="invalid upload path.",
                    thread_id=msg.thread_id,
                )
                return
    result = await _save_document_payload(
        cfg,
        document=document,
        run_root=plan.run_root,
        rel_path=rel_path,
        base_dir=base_dir,
        force=plan.force,
    )
    if result.error is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=result.error,
            thread_id=msg.thread_id,
        )
        return
    if result.rel_path is None or result.size is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to save file.",
            thread_id=msg.thread_id,
        )
        return
    context_label = _format_context(cfg.runtime, plan.resolved.context)
    await _send_plain(
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        text=(
            f"saved `{result.rel_path.as_posix()}` "
            f"in `{context_label}` ({format_bytes(result.size)})"
        ),
        thread_id=msg.thread_id,
    )


async def _handle_file_put_group(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    messages: Sequence[TelegramIncomingMessage],
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    documents = [item.document for item in messages if item.document is not None]
    if not documents:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=file_put_usage(),
            thread_id=msg.thread_id,
        )
        return
    plan = await _prepare_file_put_plan(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if plan is None:
        return
    base_dir: Path | None = None
    if plan.path_value:
        base_dir = normalize_relative_path(plan.path_value)
        if base_dir is None:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text="invalid upload path.",
                thread_id=msg.thread_id,
            )
            return
        deny_rule = deny_reason(base_dir, cfg.files.deny_globs)
        if deny_rule is not None:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text=f"path denied by rule: {deny_rule}",
                thread_id=msg.thread_id,
            )
            return
        base_target = resolve_path_within_root(plan.run_root, base_dir)
        if base_target is None:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text="upload path escapes the repo root.",
                thread_id=msg.thread_id,
            )
            return
        if base_target.exists() and not base_target.is_dir():
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text="upload path is a file.",
                thread_id=msg.thread_id,
            )
            return
    saved: list[_FilePutResult] = []
    failed: list[_FilePutResult] = []
    for document in documents:
        result = await _save_document_payload(
            cfg,
            document=document,
            run_root=plan.run_root,
            rel_path=None,
            base_dir=base_dir,
            force=plan.force,
        )
        if result.error is None:
            saved.append(result)
        else:
            failed.append(result)
    context_label = _format_context(cfg.runtime, plan.resolved.context)
    total_bytes = sum(item.size or 0 for item in saved)
    dir_label: Path | None = base_dir
    if dir_label is None and saved:
        first_path = saved[0].rel_path
        if first_path is not None:
            dir_label = first_path.parent
    if saved:
        saved_names = ", ".join(f"`{item.name}`" for item in saved)
        if dir_label is not None:
            dir_text = dir_label.as_posix()
            if not dir_text.endswith("/"):
                dir_text = f"{dir_text}/"
            text = (
                f"saved {saved_names} to `{dir_text}` "
                f"in `{context_label}` ({format_bytes(total_bytes)})"
            )
        else:
            text = (
                f"saved {saved_names} in `{context_label}` "
                f"({format_bytes(total_bytes)})"
            )
    else:
        text = "failed to upload files."
    if failed:
        errors = ", ".join(
            f"`{item.name}` ({item.error})" for item in failed if item.error is not None
        )
        if errors:
            text = f"{text}\n\nfailed: {errors}"
    await _send_plain(
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        text=text,
        thread_id=msg.thread_id,
    )


async def _handle_media_group(
    cfg: TelegramBridgeConfig,
    messages: Sequence[TelegramIncomingMessage],
    topic_store: TopicStateStore | None,
) -> None:
    if not messages:
        return
    ordered = sorted(messages, key=lambda item: item.message_id)
    command_msg = next(
        (item for item in ordered if item.text.strip()),
        ordered[0],
    )
    topic_key = _topic_key(command_msg, cfg) if topic_store is not None else None
    chat_project = (
        _topics_chat_project(cfg, command_msg.chat_id) if cfg.topics.enabled else None
    )
    bound_context = (
        await topic_store.get_context(*topic_key)
        if topic_store is not None and topic_key is not None
        else None
    )
    ambient_context = _merge_topic_context(
        chat_project=chat_project,
        bound=bound_context,
    )
    command_id, args_text = _parse_slash_command(command_msg.text)
    if command_id == "file":
        if not cfg.files.enabled:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=command_msg.chat_id,
                user_msg_id=command_msg.message_id,
                text=("file transfer disabled; enable `[transports.telegram.files]`."),
                thread_id=command_msg.thread_id,
            )
            return
        command, rest, error = parse_file_command(args_text)
        if error is not None:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=command_msg.chat_id,
                user_msg_id=command_msg.message_id,
                text=error,
                thread_id=command_msg.thread_id,
            )
            return
        if command == "put":
            await _handle_file_put_group(
                cfg,
                command_msg,
                rest,
                ordered,
                ambient_context,
                topic_store,
            )
        else:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=command_msg.chat_id,
                user_msg_id=command_msg.message_id,
                text=file_put_usage(),
                thread_id=command_msg.thread_id,
            )
        return
    if cfg.files.enabled and cfg.files.auto_put and not command_msg.text.strip():
        await _handle_file_put_group(
            cfg,
            command_msg,
            "",
            ordered,
            ambient_context,
            topic_store,
        )
        return
    if cfg.files.enabled:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=command_msg.chat_id,
            user_msg_id=command_msg.message_id,
            text=file_put_usage(),
            thread_id=command_msg.thread_id,
        )


async def _handle_file_get(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    if not await _check_file_permissions(cfg, msg):
        return
    try:
        resolved = cfg.runtime.resolve_message(
            text=args_text,
            reply_text=msg.reply_to_text,
            ambient_context=ambient_context,
            chat_id=msg.chat_id,
        )
    except DirectiveError as exc:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=f"error:\n{exc}",
            thread_id=msg.thread_id,
        )
        return
    topic_key = _topic_key(msg, cfg) if topic_store is not None else None
    await _maybe_update_topic_context(
        cfg=cfg,
        topic_store=topic_store,
        topic_key=topic_key,
        context=resolved.context,
        context_source=resolved.context_source,
    )
    if resolved.context is None or resolved.context.project is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="no project context available for file fetch.",
            thread_id=msg.thread_id,
        )
        return
    try:
        run_root = cfg.runtime.resolve_run_cwd(resolved.context)
    except ConfigError as exc:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=f"error:\n{exc}",
            thread_id=msg.thread_id,
        )
        return
    if run_root is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="no project context available for file fetch.",
            thread_id=msg.thread_id,
        )
        return
    path_value, _, error = parse_file_prompt(resolved.prompt, allow_empty=False)
    if error is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=file_get_usage(),
            thread_id=msg.thread_id,
        )
        return
    rel_path = normalize_relative_path(path_value or "")
    if rel_path is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="invalid file path.",
            thread_id=msg.thread_id,
        )
        return
    deny_rule = deny_reason(rel_path, cfg.files.deny_globs)
    if deny_rule is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=f"path denied by rule: {deny_rule}",
            thread_id=msg.thread_id,
        )
        return
    target = resolve_path_within_root(run_root, rel_path)
    if target is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="requested path escapes the repo root.",
            thread_id=msg.thread_id,
        )
        return
    if not target.exists():
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="file not found.",
            thread_id=msg.thread_id,
        )
        return
    payload: bytes
    filename: str
    if target.is_dir():
        try:
            payload = zip_directory(
                run_root,
                rel_path,
                cfg.files.deny_globs,
                max_bytes=cfg.files.max_download_bytes,
            )
        except ZipTooLargeError:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text="file is too large to send.",
                thread_id=msg.thread_id,
            )
            return
        except OSError as exc:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text=f"failed to read directory: {exc}",
                thread_id=msg.thread_id,
            )
            return
        filename = f"{rel_path.name or 'archive'}.zip"
    else:
        try:
            size = target.stat().st_size
            if size > cfg.files.max_download_bytes:
                await _send_plain(
                    cfg.exec_cfg.transport,
                    chat_id=msg.chat_id,
                    user_msg_id=msg.message_id,
                    text="file is too large to send.",
                    thread_id=msg.thread_id,
                )
                return
            payload = target.read_bytes()
        except OSError as exc:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text=f"failed to read file: {exc}",
                thread_id=msg.thread_id,
            )
            return
        filename = target.name
    if len(payload) > cfg.files.max_download_bytes:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="file is too large to send.",
            thread_id=msg.thread_id,
        )
        return
    sent = await cfg.bot.send_document(
        chat_id=msg.chat_id,
        filename=filename,
        content=payload,
        reply_to_message_id=msg.message_id,
        message_thread_id=msg.thread_id,
    )
    if sent is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to send file.",
            thread_id=msg.thread_id,
        )
        return


def _topic_title(
    *, cfg: TelegramBridgeConfig, runtime: TransportRuntime, context: RunContext
) -> str:
    project = (
        runtime.project_alias_for_key(context.project)
        if context.project is not None
        else ""
    )
    if context.branch:
        if project:
            return f"{project} @{context.branch}"
        return f"@{context.branch}"
    return project or "topic"


async def _maybe_rename_topic(
    cfg: TelegramBridgeConfig,
    store: TopicStateStore,
    *,
    chat_id: int,
    thread_id: int,
    context: RunContext,
    snapshot: TopicThreadSnapshot | None = None,
) -> None:
    title = _topic_title(cfg=cfg, runtime=cfg.runtime, context=context)
    if snapshot is None:
        snapshot = await store.get_thread(chat_id, thread_id)
    if snapshot is not None and snapshot.topic_title == title:
        return
    updated = await cfg.bot.edit_forum_topic(
        chat_id=chat_id,
        message_thread_id=thread_id,
        name=title,
    )
    if not updated:
        logger.warning(
            "topics.rename.failed",
            chat_id=chat_id,
            thread_id=thread_id,
            title=title,
        )
        return
    await store.set_context(chat_id, thread_id, context, topic_title=title)


async def _handle_ctx_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    store: TopicStateStore,
) -> None:
    error = _topics_command_error(cfg, msg.chat_id)
    if error is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=error,
            thread_id=msg.thread_id,
        )
        return
    chat_project = _topics_chat_project(cfg, msg.chat_id)
    tkey = _topic_key(msg, cfg)
    if tkey is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="this command only works inside a topic.",
            thread_id=msg.thread_id,
        )
        return
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    if action in {"show", ""}:
        snapshot = await store.get_thread(*tkey)
        bound = snapshot.context if snapshot is not None else None
        ambient = _merge_topic_context(chat_project=chat_project, bound=bound)
        resolved = cfg.runtime.resolve_message(
            text="",
            reply_text=msg.reply_to_text,
            chat_id=msg.chat_id,
            ambient_context=ambient,
        )
        text = _format_ctx_status(
            cfg=cfg,
            runtime=cfg.runtime,
            bound=bound,
            resolved=resolved.context,
            context_source=resolved.context_source,
            snapshot=snapshot,
            chat_project=chat_project,
        )
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=text,
            thread_id=msg.thread_id,
        )
        return
    if action == "set":
        rest = " ".join(tokens[1:])
        context, error = _parse_project_branch_args(
            rest,
            runtime=cfg.runtime,
            cfg=cfg,
            require_branch=False,
            chat_project=chat_project,
        )
        if error is not None:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text=f"error:\n{error}\n{_usage_ctx_set(chat_project=chat_project)}",
                thread_id=msg.thread_id,
            )
            return
        if context is None:
            await _send_plain(
                cfg.exec_cfg.transport,
                chat_id=msg.chat_id,
                user_msg_id=msg.message_id,
                text=f"error:\n{_usage_ctx_set(chat_project=chat_project)}",
                thread_id=msg.thread_id,
            )
            return
        await store.set_context(*tkey, context)
        await _maybe_rename_topic(
            cfg,
            store,
            chat_id=tkey[0],
            thread_id=tkey[1],
            context=context,
        )
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=f"topic bound to `{_format_context(cfg.runtime, context)}`",
            thread_id=msg.thread_id,
        )
        return
    if action == "clear":
        await store.clear_context(*tkey)
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="topic binding cleared.",
            thread_id=msg.thread_id,
        )
        return
    await _send_plain(
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        text="unknown `/ctx` command. use `/ctx`, `/ctx set`, or `/ctx clear`.",
        thread_id=msg.thread_id,
    )


async def _handle_new_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    store: TopicStateStore,
) -> None:
    error = _topics_command_error(cfg, msg.chat_id)
    if error is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=error,
            thread_id=msg.thread_id,
        )
        return
    tkey = _topic_key(msg, cfg)
    if tkey is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="this command only works inside a topic.",
            thread_id=msg.thread_id,
        )
        return
    await store.clear_sessions(*tkey)
    await _send_plain(
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        text="cleared stored sessions for this topic.",
        thread_id=msg.thread_id,
    )


async def _handle_topic_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    store: TopicStateStore,
) -> None:
    error = _topics_command_error(cfg, msg.chat_id)
    if error is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=error,
            thread_id=msg.thread_id,
        )
        return
    chat_project = _topics_chat_project(cfg, msg.chat_id)
    context, error = _parse_project_branch_args(
        args_text,
        runtime=cfg.runtime,
        cfg=cfg,
        require_branch=True,
        chat_project=chat_project,
    )
    if error is not None or context is None:
        usage = _usage_topic(chat_project=chat_project)
        text = f"error:\n{error}\n{usage}" if error else usage
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=text,
            thread_id=msg.thread_id,
        )
        return
    target_chat_id = msg.chat_id
    existing = await store.find_thread_for_context(target_chat_id, context)
    if existing is not None:
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text=f"topic already exists for {_format_context(cfg.runtime, context)} "
            "in this chat.",
            thread_id=msg.thread_id,
        )
        return
    title = _topic_title(cfg=cfg, runtime=cfg.runtime, context=context)
    created = await cfg.bot.create_forum_topic(target_chat_id, title)
    thread_id = created.get("message_thread_id") if isinstance(created, dict) else None
    if isinstance(thread_id, bool) or not isinstance(thread_id, int):
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            text="failed to create topic.",
            thread_id=msg.thread_id,
        )
        return
    await store.set_context(
        target_chat_id,
        thread_id,
        context,
        topic_title=title,
        created_by_bot=True,
    )
    await _send_plain(
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        text=f"created topic `{title}`.",
        thread_id=msg.thread_id,
    )
    await cfg.exec_cfg.transport.send(
        channel_id=target_chat_id,
        message=RenderedMessage(
            text=f"topic bound to `{_format_context(cfg.runtime, context)}`"
        ),
        options=SendOptions(thread_id=thread_id),
    )


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
                thread_id=msg.thread_id,
            )
            return
        await _send_plain(
            cfg.exec_cfg.transport,
            chat_id=chat_id,
            user_msg_id=user_msg_id,
            text="reply to the progress message to cancel.",
            thread_id=msg.thread_id,
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
            thread_id=msg.thread_id,
        )
        return

    logger.info(
        "cancel.requested",
        chat_id=chat_id,
        progress_message_id=reply_id,
    )
    running_task.cancel_requested.set()


async def _handle_callback_cancel(
    cfg: TelegramBridgeConfig,
    query: TelegramCallbackQuery,
    running_tasks: RunningTasks,
) -> None:
    progress_ref = MessageRef(channel_id=query.chat_id, message_id=query.message_id)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await cfg.bot.answer_callback_query(
            callback_query_id=query.callback_query_id,
            text="nothing is currently running for that message.",
        )
        return
    logger.info(
        "cancel.requested",
        chat_id=query.chat_id,
        progress_message_id=query.message_id,
    )
    running_task.cancel_requested.set()
    await cfg.bot.answer_callback_query(
        callback_query_id=query.callback_query_id,
        text="cancelling...",
    )


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
    enqueue: Callable[
        [int, int, str, ResumeToken, RunContext | None, int | None], Awaitable[None]
    ],
    running_task: RunningTask,
    chat_id: int,
    user_msg_id: int,
    thread_id: int | None,
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
            thread_id=thread_id,
        )
        return
    await enqueue(
        chat_id,
        user_msg_id,
        text,
        resume,
        running_task.context,
        thread_id,
    )


async def _send_runner_unavailable(
    exec_cfg: ExecBridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    resume_token: ResumeToken | None,
    runner: Runner,
    reason: str,
    thread_id: int | None = None,
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
        options=SendOptions(reply_to=reply_to, notify=True, thread_id=thread_id),
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
    thread_id: int | None = None,
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
                thread_id=thread_id,
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
                thread_id=thread_id,
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
                thread_id=thread_id,
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
                thread_id=thread_id,
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
        thread_id: int | None,
    ) -> None:
        self._exec_cfg = exec_cfg
        self._runtime = runtime
        self._running_tasks = running_tasks
        self._scheduler = scheduler
        self._chat_id = chat_id
        self._user_msg_id = user_msg_id
        self._thread_id = thread_id
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
            options=SendOptions(
                reply_to=reply_ref,
                notify=notify,
                thread_id=self._thread_id,
            ),
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
                thread_id=self._thread_id,
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
            thread_id=self._thread_id,
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
        thread_id=msg.thread_id,
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
        args=split_command_args(args_text),
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
    poller: Callable[
        [TelegramBridgeConfig], AsyncIterator[TelegramIncomingUpdate]
    ] = poll_updates,
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
    topic_store: TopicStateStore | None = None
    media_groups: dict[tuple[int, str], _MediaGroupState] = {}

    try:
        if cfg.topics.enabled:
            config_path = cfg.runtime.config_path
            if config_path is None:
                raise ConfigError(
                    "topics enabled but config path is not set; cannot locate state file."
                )
            topic_store = TopicStateStore(resolve_state_path(config_path))
            await _validate_topics_setup(cfg)
            resolved_scope, _ = _resolve_topics_scope(cfg)
            logger.info(
                "topics.enabled",
                scope=cfg.topics.scope,
                resolved_scope=resolved_scope,
                state_path=str(resolve_state_path(config_path)),
            )
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

            def wrap_on_thread_known(
                base_cb: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
                topic_key: tuple[int, int] | None,
            ) -> Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None:
                if base_cb is None and topic_key is None:
                    return None

                async def _wrapped(token: ResumeToken, done: anyio.Event) -> None:
                    if base_cb is not None:
                        await base_cb(token, done)
                    if topic_store is not None and topic_key is not None:
                        await topic_store.set_session_resume(
                            topic_key[0], topic_key[1], token
                        )

                return _wrapped

            async def run_job(
                chat_id: int,
                user_msg_id: int,
                text: str,
                resume_token: ResumeToken | None,
                context: RunContext | None,
                thread_id: int | None = None,
                reply_ref: MessageRef | None = None,
                on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
                | None = None,
                engine_override: EngineId | None = None,
            ) -> None:
                topic_key = (
                    (chat_id, thread_id)
                    if topic_store is not None
                    and thread_id is not None
                    and _topics_chat_allowed(cfg, chat_id)
                    else None
                )
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
                    on_thread_known=wrap_on_thread_known(on_thread_known, topic_key),
                    engine_override=engine_override,
                    thread_id=thread_id,
                )

            async def run_thread_job(job: ThreadJob) -> None:
                await run_job(
                    job.chat_id,
                    job.user_msg_id,
                    job.text,
                    job.resume_token,
                    job.context,
                    job.thread_id,
                    None,
                    scheduler.note_thread_known,
                )

            scheduler = ThreadScheduler(task_group=tg, run_job=run_thread_job)

            async def flush_media_group(key: tuple[int, str]) -> None:
                while True:
                    state = media_groups.get(key)
                    if state is None:
                        return
                    token = state.token
                    await anyio.sleep(_MEDIA_GROUP_DEBOUNCE_S)
                    state = media_groups.get(key)
                    if state is None:
                        return
                    if state.token != token:
                        continue
                    messages = list(state.messages)
                    del media_groups[key]
                    await _handle_media_group(cfg, messages, topic_store)
                    return

            async for msg in poller(cfg):
                if isinstance(msg, TelegramCallbackQuery):
                    if msg.data == CANCEL_CALLBACK_DATA:
                        tg.start_soon(_handle_callback_cancel, cfg, msg, running_tasks)
                    else:
                        tg.start_soon(
                            cfg.bot.answer_callback_query,
                            msg.callback_query_id,
                        )
                    continue
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
                topic_key = _topic_key(msg, cfg) if topic_store is not None else None
                chat_project = (
                    _topics_chat_project(cfg, chat_id) if cfg.topics.enabled else None
                )
                bound_context = (
                    await topic_store.get_context(*topic_key)
                    if topic_store is not None and topic_key is not None
                    else None
                )
                ambient_context = _merge_topic_context(
                    chat_project=chat_project, bound=bound_context
                )

                if (
                    cfg.files.enabled
                    and msg.document is not None
                    and msg.media_group_id is not None
                ):
                    key = (chat_id, msg.media_group_id)
                    state = media_groups.get(key)
                    if state is None:
                        state = _MediaGroupState(messages=[])
                        media_groups[key] = state
                        tg.start_soon(flush_media_group, key)
                    state.messages.append(msg)
                    state.token += 1
                    continue

                if _is_cancel_command(text):
                    tg.start_soon(_handle_cancel, cfg, msg, running_tasks)
                    continue

                command_id, args_text = _parse_slash_command(text)
                if command_id == "file":
                    if not cfg.files.enabled:
                        tg.start_soon(
                            partial(
                                _send_plain,
                                cfg.exec_cfg.transport,
                                chat_id=chat_id,
                                user_msg_id=user_msg_id,
                                text=(
                                    "file transfer disabled; enable "
                                    "`[transports.telegram.files]`."
                                ),
                                thread_id=msg.thread_id,
                            )
                        )
                    else:
                        tg.start_soon(
                            _handle_file_command,
                            cfg,
                            msg,
                            args_text,
                            ambient_context,
                            topic_store,
                        )
                    continue
                if msg.document is not None:
                    if cfg.files.enabled and cfg.files.auto_put and not text.strip():
                        tg.start_soon(
                            _handle_file_put_default,
                            cfg,
                            msg,
                            ambient_context,
                            topic_store,
                        )
                    elif cfg.files.enabled:
                        tg.start_soon(
                            partial(
                                _send_plain,
                                cfg.exec_cfg.transport,
                                chat_id=chat_id,
                                user_msg_id=user_msg_id,
                                text=file_put_usage(),
                                thread_id=msg.thread_id,
                            )
                        )
                    continue
                if (
                    cfg.topics.enabled
                    and topic_store is not None
                    and command_id in _TOPICS_COMMANDS
                ):
                    if command_id == "ctx":
                        tg.start_soon(
                            _handle_ctx_command, cfg, msg, args_text, topic_store
                        )
                    elif command_id == "new":
                        tg.start_soon(_handle_new_command, cfg, msg, topic_store)
                    else:
                        tg.start_soon(
                            _handle_topic_command, cfg, msg, args_text, topic_store
                        )
                    continue
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
                        ambient_context=ambient_context,
                        chat_id=chat_id,
                    )
                except DirectiveError as exc:
                    await _send_plain(
                        cfg.exec_cfg.transport,
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        text=f"error:\n{exc}",
                        thread_id=msg.thread_id,
                    )
                    continue

                text = resolved.prompt
                resume_token = resolved.resume_token
                engine_override = resolved.engine_override
                context = resolved.context
                if (
                    topic_store is not None
                    and topic_key is not None
                    and resolved.context is not None
                    and resolved.context_source == "directives"
                ):
                    await topic_store.set_context(*topic_key, resolved.context)
                    await _maybe_rename_topic(
                        cfg,
                        topic_store,
                        chat_id=topic_key[0],
                        thread_id=topic_key[1],
                        context=resolved.context,
                    )
                    ambient_context = resolved.context
                if (
                    topic_store is not None
                    and topic_key is not None
                    and ambient_context is None
                    and resolved.context_source not in {"directives", "reply_ctx"}
                ):
                    await _send_plain(
                        cfg.exec_cfg.transport,
                        chat_id=chat_id,
                        user_msg_id=user_msg_id,
                        text=(
                            "this topic isn't bound to a project yet.\n"
                            f"{_usage_ctx_set(chat_project=chat_project)} or "
                            f"{_usage_topic(chat_project=chat_project)}"
                        ),
                        thread_id=msg.thread_id,
                    )
                    continue
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
                            msg.thread_id,
                            text,
                        )
                        continue
                if (
                    resume_token is None
                    and topic_store is not None
                    and topic_key is not None
                ):
                    engine_for_session = cfg.runtime.resolve_engine(
                        engine_override=engine_override,
                        context=context,
                    )
                    stored = await topic_store.get_session_resume(
                        topic_key[0], topic_key[1], engine_for_session
                    )
                    if stored is not None:
                        resume_token = stored

                if resume_token is None:
                    tg.start_soon(
                        run_job,
                        chat_id,
                        user_msg_id,
                        text,
                        None,
                        context,
                        msg.thread_id,
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
                        msg.thread_id,
                    )
    finally:
        await cfg.exec_cfg.transport.close()
