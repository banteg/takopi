from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from ...context import RunContext
from ...directives import DirectiveError
from ...transport_runtime import ResolvedMessage
from ..context import _merge_topic_context
from ..files import parse_file_command
from ..topic_state import TopicStateStore
from ..topics import _topic_key, _topics_chat_project
from ..types import TelegramIncomingMessage
from .file_transfer import (
    FILE_PUT_USAGE,
    _format_file_put_failures,
    _handle_file_put_group,
    _save_file_put_group,
)
from .parse import _parse_slash_command
from .reply import make_reply

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig


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
    reply = make_reply(cfg, command_msg)
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
