from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import cast

import anyio

from ..commands import (
    CommandContext,
    CommandExecutor,
    RunMode,
    RunRequest,
    RunResult,
    get_command,
)
from ..context import RunContext
from ..config import ConfigError
from ..directives import DirectiveError
from ..ids import RESERVED_COMMAND_IDS, is_valid_id
from ..logging import bind_run_context, clear_context, get_logger
from ..markdown import MarkdownParts
from ..model import EngineId, ResumeToken, TakopiEvent
from ..plugins import COMMAND_GROUP, list_entrypoints
from ..progress import ProgressTracker
from ..router import RunnerUnavailableError
from ..runner import Runner
from ..runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage as RunnerIncomingMessage,
    RunningTasks,
    handle_message,
)
from ..scheduler import ThreadScheduler
from ..transport import MessageRef, RenderedMessage, SendOptions
from ..transport_runtime import ResolvedMessage, TransportRuntime
from ..utils.paths import reset_run_base_dir, set_run_base_dir
from .bridge import TelegramBridgeConfig, send_plain
from .chat_sessions import ChatSessionStore
from .context import (
    _format_context,
    _format_ctx_status,
    _merge_topic_context,
    _parse_project_branch_args,
    _usage_ctx_set,
    _usage_topic,
)
from .files import (
    default_upload_name,
    default_upload_path,
    deny_reason,
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
from .render import prepare_telegram
from .topic_state import TopicStateStore
from .topics import (
    _maybe_rename_topic,
    _maybe_update_topic_context,
    _topic_key,
    _topic_title,
    _topics_chat_project,
    _topics_command_error,
)
from .types import TelegramCallbackQuery, TelegramDocument, TelegramIncomingMessage

logger = get_logger(__name__)

__all__ = [
    "FILE_GET_USAGE",
    "FILE_PUT_USAGE",
    "_dispatch_command",
    "_handle_chat_new_command",
    "_handle_file_command",
    "_handle_file_get",
    "_handle_file_put",
    "_handle_file_put_default",
    "_handle_media_group",
    "_parse_slash_command",
    "_reserved_commands",
    "_set_command_menu",
    "build_bot_commands",
    "handle_callback_cancel",
    "handle_cancel",
    "is_cancel_command",
]

_MAX_BOT_COMMANDS = 100
FILE_PUT_USAGE = "usage: `/file put <path>`"
FILE_GET_USAGE = "usage: `/file get <path>`"


def is_cancel_command(text: str) -> bool:
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


def build_bot_commands(
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


def _reply_sender(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> Callable[..., Awaitable[None]]:
    return partial(
        send_plain,
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        thread_id=msg.thread_id,
    )


async def _set_command_menu(cfg: TelegramBridgeConfig) -> None:
    commands = build_bot_commands(cfg.runtime, include_file=cfg.files.enabled)
    if not commands:
        return
    try:
        ok = await cfg.bot.set_my_commands(commands)
    except Exception as exc:  # noqa: BLE001
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
class _SavedFilePut:
    context: RunContext | None
    rel_path: Path
    size: int


@dataclass(slots=True)
class _SavedFilePutGroup:
    context: RunContext | None
    base_dir: Path | None
    saved: list[_FilePutResult]
    failed: list[_FilePutResult]


@dataclass(slots=True)
class _ResumeLineProxy:
    runner: Runner

    @property
    def engine(self) -> str:
        return self.runner.engine

    def is_resume_line(self, line: str) -> bool:
        return self.runner.is_resume_line(line)

    def format_resume(self, _: ResumeToken) -> str:
        return ""

    def extract_resume(self, text: str | None) -> ResumeToken | None:
        return self.runner.extract_resume(text)

    def run(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[TakopiEvent]:
        return self.runner.run(prompt, resume)


def _should_show_resume_line(
    *,
    show_resume_line: bool,
    stateful_mode: bool,
    context: RunContext | None,
) -> bool:
    if show_resume_line:
        return True
    if not stateful_mode:
        return True
    if context is None or context.project is None:
        return True
    return False


def resolve_file_put_paths(
    plan: _FilePutPlan,
    *,
    cfg: TelegramBridgeConfig,
    require_dir: bool,
) -> tuple[Path | None, Path | None, str | None]:
    path_value = plan.path_value
    if not path_value:
        return None, None, None
    if require_dir or path_value.endswith("/"):
        base_dir = normalize_relative_path(path_value)
        if base_dir is None:
            return None, None, "invalid upload path."
        deny_rule = deny_reason(base_dir, cfg.files.deny_globs)
        if deny_rule is not None:
            return None, None, f"path denied by rule: {deny_rule}"
        base_target = resolve_path_within_root(plan.run_root, base_dir)
        if base_target is None:
            return None, None, "upload path escapes the repo root."
        if base_target.exists() and not base_target.is_dir():
            return None, None, "upload path is a file."
        return base_dir, None, None
    rel_path = normalize_relative_path(path_value)
    if rel_path is None:
        return None, None, "invalid upload path."
    return None, rel_path, None


async def _check_file_permissions(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> bool:
    reply = _reply_sender(cfg, msg)
    sender_id = msg.sender_id
    if sender_id is None:
        await reply(text="cannot verify sender for file transfer.")
        return False
    if cfg.files.allowed_user_ids:
        if sender_id not in cfg.files.allowed_user_ids:
            await reply(text="file transfer is not allowed for this user.")
            return False
        return True
    is_private = msg.chat_type == "private"
    if msg.chat_type is None:
        is_private = msg.chat_id > 0
    if is_private:
        return True
    member = await cfg.bot.get_chat_member(msg.chat_id, sender_id)
    if member is None:
        await reply(text="failed to verify file transfer permissions.")
        return False
    if member.status in {"creator", "administrator"}:
        return True
    await reply(text="file transfer is restricted to group admins.")
    return False


async def _prepare_file_put_plan(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> _FilePutPlan | None:
    reply = _reply_sender(cfg, msg)
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
        await reply(text=f"error:\n{exc}")
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
        await reply(text="no project context available for file upload.")
        return None
    try:
        run_root = cfg.runtime.resolve_run_cwd(resolved.context)
    except ConfigError as exc:
        await reply(text=f"error:\n{exc}")
        return None
    if run_root is None:
        await reply(text="no project context available for file upload.")
        return None
    path_value, force, error = parse_file_prompt(resolved.prompt, allow_empty=True)
    if error is not None:
        await reply(text=error)
        return None
    return _FilePutPlan(
        resolved=resolved,
        run_root=run_root,
        path_value=path_value,
        force=force,
    )


def _format_file_put_failures(failed: Sequence[_FilePutResult]) -> str | None:
    if not failed:
        return None
    errors = ", ".join(
        f"`{item.name}` ({item.error})" for item in failed if item.error is not None
    )
    if not errors:
        return None
    return f"failed: {errors}"


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
    if file_info is None:
        return _FilePutResult(
            name=name,
            rel_path=None,
            size=None,
            error="failed to fetch file metadata.",
        )
    file_path = file_info.file_path
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


async def _handle_file_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = _reply_sender(cfg, msg)
    command, rest, error = parse_file_command(args_text)
    if error is not None:
        await reply(text=error)
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


async def _save_file_put(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> _SavedFilePut | None:
    reply = _reply_sender(cfg, msg)
    document = msg.document
    if document is None:
        await reply(text=FILE_PUT_USAGE)
        return None
    plan = await _prepare_file_put_plan(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if plan is None:
        return None
    base_dir, rel_path, error = resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=False,
    )
    if error is not None:
        await reply(text=error)
        return None
    result = await _save_document_payload(
        cfg,
        document=document,
        run_root=plan.run_root,
        rel_path=rel_path,
        base_dir=base_dir,
        force=plan.force,
    )
    if result.error is not None:
        await reply(text=result.error)
        return None
    if result.rel_path is None or result.size is None:
        await reply(text="failed to save file.")
        return None
    return _SavedFilePut(
        context=plan.resolved.context,
        rel_path=result.rel_path,
        size=result.size,
    )


async def _handle_file_put(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = _reply_sender(cfg, msg)
    saved = await _save_file_put(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if saved is None:
        return
    context_label = _format_context(cfg.runtime, saved.context)
    await reply(
        text=(
            f"saved `{saved.rel_path.as_posix()}` "
            f"in `{context_label}` ({format_bytes(saved.size)})"
        ),
    )


async def _handle_file_put_group(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    messages: Sequence[TelegramIncomingMessage],
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = _reply_sender(cfg, msg)
    saved_group = await _save_file_put_group(
        cfg,
        msg,
        args_text,
        messages,
        ambient_context,
        topic_store,
    )
    if saved_group is None:
        return
    context_label = _format_context(cfg.runtime, saved_group.context)
    total_bytes = sum(item.size or 0 for item in saved_group.saved)
    dir_label: Path | None = saved_group.base_dir
    if dir_label is None and saved_group.saved:
        first_path = saved_group.saved[0].rel_path
        if first_path is not None:
            dir_label = first_path.parent
    if saved_group.saved:
        saved_names = ", ".join(f"`{item.name}`" for item in saved_group.saved)
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
    failure_text = _format_file_put_failures(saved_group.failed)
    if failure_text is not None:
        text = f"{text}\n\n{failure_text}"
    await reply(text=text)


async def _save_file_put_group(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    messages: Sequence[TelegramIncomingMessage],
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> _SavedFilePutGroup | None:
    reply = _reply_sender(cfg, msg)
    documents = [item.document for item in messages if item.document is not None]
    if not documents:
        await reply(text=FILE_PUT_USAGE)
        return None
    plan = await _prepare_file_put_plan(
        cfg,
        msg,
        args_text,
        ambient_context,
        topic_store,
    )
    if plan is None:
        return None
    base_dir, _, error = resolve_file_put_paths(
        plan,
        cfg=cfg,
        require_dir=True,
    )
    if error is not None:
        await reply(text=error)
        return None
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
    return _SavedFilePutGroup(
        context=plan.resolved.context,
        base_dir=base_dir,
        saved=saved,
        failed=failed,
    )


async def _handle_media_group(
    cfg: TelegramBridgeConfig,
    messages: Sequence[TelegramIncomingMessage],
    topic_store: TopicStateStore | None,
    run_prompt: Callable[
        [TelegramIncomingMessage, str, ResolvedMessage], Awaitable[None]
    ]
    | None = None,
    resolve_prompt: Callable[
        [TelegramIncomingMessage, str, RunContext | None],
        Awaitable[ResolvedMessage | None],
    ]
    | None = None,
) -> None:
    if not messages:
        return
    ordered = sorted(messages, key=lambda item: item.message_id)
    command_msg = next(
        (item for item in ordered if item.text.strip()),
        ordered[0],
    )
    reply = _reply_sender(cfg, command_msg)
    topic_key = _topic_key(command_msg, cfg) if topic_store is not None else None
    chat_project = _topics_chat_project(cfg, command_msg.chat_id)
    bound_context = (
        await topic_store.get_context(*topic_key)
        if topic_store is not None and topic_key is not None
        else None
    )
    ambient_context = _merge_topic_context(
        chat_project=chat_project, bound=bound_context
    )
    command_id, args_text = _parse_slash_command(command_msg.text)
    if command_id == "file":
        command, rest, error = parse_file_command(args_text)
        if error is not None:
            await reply(text=error)
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
            return
    if cfg.files.enabled and cfg.files.auto_put:
        caption_text = command_msg.text.strip()
        if cfg.files.auto_put_mode == "prompt" and caption_text:
            if resolve_prompt is None:
                try:
                    resolved = cfg.runtime.resolve_message(
                        text=caption_text,
                        reply_text=command_msg.reply_to_text,
                        ambient_context=ambient_context,
                        chat_id=command_msg.chat_id,
                    )
                except DirectiveError as exc:
                    await reply(text=f"error:\n{exc}")
                    return
            else:
                resolved = await resolve_prompt(
                    command_msg, caption_text, ambient_context
                )
            if resolved is None:
                return
            saved_group = await _save_file_put_group(
                cfg,
                command_msg,
                "",
                ordered,
                resolved.context,
                topic_store,
            )
            if saved_group is None:
                return
            if not saved_group.saved:
                failure_text = _format_file_put_failures(saved_group.failed)
                text = "failed to upload files."
                if failure_text is not None:
                    text = f"{text}\n\n{failure_text}"
                await reply(text=text)
                return
            if saved_group.failed:
                failure_text = _format_file_put_failures(saved_group.failed)
                if failure_text is not None:
                    await reply(text=f"some files failed to upload.\n\n{failure_text}")
            if run_prompt is None:
                await reply(text=FILE_PUT_USAGE)
                return
            paths = [
                item.rel_path.as_posix()
                for item in saved_group.saved
                if item.rel_path is not None
            ]
            files_text = "\n".join(f"- {path}" for path in paths)
            prompt_base = resolved.prompt
            annotation = f"[uploaded files]\n{files_text}"
            if prompt_base and prompt_base.strip():
                prompt = f"{prompt_base}\n\n{annotation}"
            else:
                prompt = annotation
            await run_prompt(command_msg, prompt, resolved)
            return
        if not caption_text:
            await _handle_file_put_group(
                cfg,
                command_msg,
                "",
                ordered,
                ambient_context,
                topic_store,
            )
            return
    await reply(text=FILE_PUT_USAGE)


async def _handle_file_get(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
) -> None:
    reply = _reply_sender(cfg, msg)
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
        await reply(text=f"error:\n{exc}")
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
        await reply(text="no project context available for file download.")
        return
    try:
        run_root = cfg.runtime.resolve_run_cwd(resolved.context)
    except ConfigError as exc:
        await reply(text=f"error:\n{exc}")
        return
    if run_root is None:
        await reply(text="no project context available for file download.")
        return
    path_value = resolved.prompt
    if not path_value.strip():
        await reply(text=FILE_GET_USAGE)
        return
    rel_path = normalize_relative_path(path_value)
    if rel_path is None:
        await reply(text="invalid download path.")
        return
    deny_rule = deny_reason(rel_path, cfg.files.deny_globs)
    if deny_rule is not None:
        await reply(text=f"path denied by rule: {deny_rule}")
        return
    target = resolve_path_within_root(run_root, rel_path)
    if target is None:
        await reply(text="download path escapes the repo root.")
        return
    if not target.exists():
        await reply(text="file does not exist.")
        return
    if target.is_dir():
        try:
            payload = zip_directory(
                run_root,
                rel_path,
                cfg.files.deny_globs,
                max_bytes=cfg.files.max_download_bytes,
            )
        except ZipTooLargeError:
            await reply(text="file is too large to send.")
            return
        except OSError as exc:
            await reply(text=f"failed to read directory: {exc}")
            return
        filename = f"{rel_path.name or 'archive'}.zip"
    else:
        try:
            size = target.stat().st_size
            if size > cfg.files.max_download_bytes:
                await reply(text="file is too large to send.")
                return
            payload = target.read_bytes()
        except OSError as exc:
            await reply(text=f"failed to read file: {exc}")
            return
        filename = target.name
    if len(payload) > cfg.files.max_download_bytes:
        await reply(text="file is too large to send.")
        return
    sent = await cfg.bot.send_document(
        chat_id=msg.chat_id,
        filename=filename,
        content=payload,
        reply_to_message_id=msg.message_id,
        message_thread_id=msg.thread_id,
    )
    if sent is None:
        await reply(text="failed to send file.")
        return


async def _handle_ctx_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    store: TopicStateStore,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    reply = _reply_sender(cfg, msg)
    error = _topics_command_error(
        cfg,
        msg.chat_id,
        resolved_scope=resolved_scope,
        scope_chat_ids=scope_chat_ids,
    )
    if error is not None:
        await reply(text=error)
        return
    chat_project = _topics_chat_project(cfg, msg.chat_id)
    tkey = _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)
    if tkey is None:
        await reply(text="this command only works inside a topic.")
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
        await reply(text=text)
        return
    if action == "set":
        rest = " ".join(tokens[1:])
        context, error = _parse_project_branch_args(
            rest,
            runtime=cfg.runtime,
            require_branch=False,
            chat_project=chat_project,
        )
        if error is not None:
            await reply(
                text=f"error:\n{error}\n{_usage_ctx_set(chat_project=chat_project)}",
            )
            return
        if context is None:
            await reply(
                text=f"error:\n{_usage_ctx_set(chat_project=chat_project)}",
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
        await reply(
            text=f"topic bound to `{_format_context(cfg.runtime, context)}`",
        )
        return
    if action == "clear":
        await store.clear_context(*tkey)
        await reply(text="topic binding cleared.")
        return
    await reply(
        text="unknown `/ctx` command. use `/ctx`, `/ctx set`, or `/ctx clear`.",
    )


async def _handle_new_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    store: TopicStateStore,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    reply = _reply_sender(cfg, msg)
    error = _topics_command_error(
        cfg,
        msg.chat_id,
        resolved_scope=resolved_scope,
        scope_chat_ids=scope_chat_ids,
    )
    if error is not None:
        await reply(text=error)
        return
    tkey = _topic_key(msg, cfg, scope_chat_ids=scope_chat_ids)
    if tkey is None:
        await reply(text="this command only works inside a topic.")
        return
    await store.clear_sessions(*tkey)
    await reply(text="cleared stored sessions for this topic.")


async def _handle_chat_new_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    store: ChatSessionStore,
    session_key: tuple[int, int | None] | None,
) -> None:
    reply = _reply_sender(cfg, msg)
    if session_key is None:
        await reply(text="no stored sessions to clear for this chat.")
        return
    await store.clear_sessions(session_key[0], session_key[1])
    if msg.chat_type == "private":
        text = "cleared stored sessions for this chat."
    else:
        text = "cleared stored sessions for you in this chat."
    await reply(text=text)


async def _handle_topic_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    store: TopicStateStore,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    reply = _reply_sender(cfg, msg)
    error = _topics_command_error(
        cfg,
        msg.chat_id,
        resolved_scope=resolved_scope,
        scope_chat_ids=scope_chat_ids,
    )
    if error is not None:
        await reply(text=error)
        return
    chat_project = _topics_chat_project(cfg, msg.chat_id)
    context, error = _parse_project_branch_args(
        args_text,
        runtime=cfg.runtime,
        require_branch=True,
        chat_project=chat_project,
    )
    if error is not None or context is None:
        usage = _usage_topic(chat_project=chat_project)
        text = f"error:\n{error}\n{usage}" if error else usage
        await reply(text=text)
        return
    existing = await store.find_thread_for_context(msg.chat_id, context)
    if existing is not None:
        await reply(
            text=f"topic already exists for {_format_context(cfg.runtime, context)} "
            "in this chat.",
        )
        return
    title = _topic_title(runtime=cfg.runtime, context=context)
    created = await cfg.bot.create_forum_topic(msg.chat_id, title)
    if created is None:
        await reply(text="failed to create topic.")
        return
    thread_id = created.message_thread_id
    await store.set_context(
        msg.chat_id,
        thread_id,
        context,
        topic_title=title,
    )
    await reply(text=f"created topic `{title}`.")
    bound_text = f"topic bound to `{_format_context(cfg.runtime, context)}`"
    rendered_text, entities = prepare_telegram(MarkdownParts(header=bound_text))
    await cfg.exec_cfg.transport.send(
        channel_id=msg.chat_id,
        message=RenderedMessage(text=rendered_text, extra={"entities": entities}),
        options=SendOptions(thread_id=thread_id),
    )


async def handle_cancel(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    running_tasks: RunningTasks,
) -> None:
    reply = _reply_sender(cfg, msg)
    chat_id = msg.chat_id
    reply_id = msg.reply_to_message_id

    if reply_id is None:
        if msg.reply_to_text:
            await reply(text="nothing is currently running for that message.")
            return
        await reply(text="reply to the progress message to cancel.")
        return

    progress_ref = MessageRef(channel_id=chat_id, message_id=reply_id)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await reply(text="nothing is currently running for that message.")
        return

    logger.info(
        "cancel.requested",
        chat_id=chat_id,
        progress_message_id=reply_id,
    )
    running_task.cancel_requested.set()


async def handle_callback_cancel(
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
    show_resume_line: bool = True,
) -> None:
    reply = partial(
        send_plain,
        exec_cfg.transport,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        thread_id=thread_id,
    )
    try:
        try:
            entry = runtime.resolve_runner(
                resume_token=resume_token,
                engine_override=engine_override,
            )
        except RunnerUnavailableError as exc:
            await reply(text=f"error:\n{exc}")
            return
        runner: Runner = entry.runner
        if not show_resume_line:
            runner = cast(Runner, _ResumeLineProxy(runner))
        if not entry.available:
            reason = entry.issue or "engine unavailable"
            await _send_runner_unavailable(
                exec_cfg,
                chat_id=chat_id,
                user_msg_id=user_msg_id,
                resume_token=resume_token,
                runner=runner,
                reason=reason,
                thread_id=thread_id,
            )
            return
        try:
            cwd = runtime.resolve_run_cwd(context)
        except ConfigError as exc:
            await reply(text=f"error:\n{exc}")
            return
        run_base_token = set_run_base_dir(cwd)
        try:
            run_fields = {
                "chat_id": chat_id,
                "user_msg_id": user_msg_id,
                "engine": runner.engine,
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
                runner=runner,
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
        thread_id = options.thread_id if options is not None else None
        ref = MessageRef(channel_id=channel_id, message_id=self._next_id)
        self._next_id += 1
        self.last_message = message
        return MessageRef(
            channel_id=ref.channel_id,
            message_id=ref.message_id,
            thread_id=thread_id,
        )

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
        on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
        chat_id: int,
        user_msg_id: int,
        thread_id: int | None,
        show_resume_line: bool,
        stateful_mode: bool,
    ) -> None:
        self._exec_cfg = exec_cfg
        self._runtime = runtime
        self._running_tasks = running_tasks
        self._scheduler = scheduler
        self._on_thread_known = on_thread_known
        self._chat_id = chat_id
        self._user_msg_id = user_msg_id
        self._thread_id = thread_id
        self._show_resume_line = show_resume_line
        self._stateful_mode = stateful_mode
        self._reply_ref = MessageRef(
            channel_id=chat_id,
            message_id=user_msg_id,
            thread_id=thread_id,
        )

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
        effective_show_resume_line = _should_show_resume_line(
            show_resume_line=self._show_resume_line,
            stateful_mode=self._stateful_mode,
            context=request.context,
        )
        engine = self._runtime.resolve_engine(
            engine_override=request.engine,
            context=request.context,
        )
        on_thread_known = (
            self._scheduler.note_thread_known
            if self._on_thread_known is None
            else self._on_thread_known
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
                on_thread_known=on_thread_known,
                engine_override=engine,
                thread_id=self._thread_id,
                show_resume_line=effective_show_resume_line,
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
            on_thread_known=on_thread_known,
            engine_override=engine,
            thread_id=self._thread_id,
            show_resume_line=effective_show_resume_line,
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
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
    stateful_mode: bool,
) -> None:
    allowlist = cfg.runtime.allowlist
    chat_id = msg.chat_id
    user_msg_id = msg.message_id
    reply_ref = (
        MessageRef(
            channel_id=chat_id,
            message_id=msg.reply_to_message_id,
            thread_id=msg.thread_id,
        )
        if msg.reply_to_message_id is not None
        else None
    )
    executor = _TelegramCommandExecutor(
        exec_cfg=cfg.exec_cfg,
        runtime=cfg.runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        on_thread_known=on_thread_known,
        chat_id=chat_id,
        user_msg_id=user_msg_id,
        thread_id=msg.thread_id,
        show_resume_line=cfg.show_resume_line,
        stateful_mode=stateful_mode,
    )
    message_ref = MessageRef(
        channel_id=chat_id,
        message_id=user_msg_id,
        thread_id=msg.thread_id,
        sender_id=msg.sender_id,
        raw=msg.raw,
    )
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
