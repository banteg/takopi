from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, cast

from ..logging import get_logger
from ..markdown import MarkdownFormatter, MarkdownParts
from ..progress import ProgressState
from ..runner_bridge import ExecBridgeConfig, RunningTask, RunningTasks
from ..transport import MessageRef, RenderedMessage, SendOptions, Transport
from ..transport_runtime import TransportRuntime
from ..context import RunContext
from ..model import ResumeToken
from ..scheduler import ThreadScheduler
from ..settings import (
    TelegramFilesSettings,
    TelegramTopicsSettings,
    TelegramTransportSettings,
)
from .client import BotClient
from .render import MAX_BODY_CHARS, prepare_telegram, prepare_telegram_multi
from .types import TelegramCallbackQuery, TelegramIncomingMessage

logger = get_logger(__name__)

__all__ = [
    "PROMPT_CALLBACK_PREFIX",
    "TelegramBridgeConfig",
    "TelegramPresenter",
    "TelegramTransport",
    "build_bot_commands",
    "format_prompt_text",
    "handle_callback_cancel",
    "handle_callback_prompt",
    "handle_cancel",
    "is_cancel_command",
    "prompt_markup",
    "run_main_loop",
    "send_with_resume",
]

CANCEL_CALLBACK_DATA = "takopi:cancel"
CANCEL_MARKUP = {
    "inline_keyboard": [[{"text": "cancel", "callback_data": CANCEL_CALLBACK_DATA}]]
}
CLEAR_MARKUP = {"inline_keyboard": []}

PROMPT_CALLBACK_PREFIX = "takopi:prompt:"

# v1: only allow/deny. allow_always omitted until Claude's ControlSuccessResponse
# shape for "always allow" is verified against the wire protocol.
_KNOWN_ACTIONS: dict[str, str] = {
    "allow": "Allow",
    "deny": "Deny",
}


def prompt_markup(
    local_id: str,
    permission_suggestions: list[object] | None,
    *,
    progress_msg_id: object,
) -> dict:
    suggestions = ["allow", "deny"]
    if permission_suggestions:
        filtered = [str(s) for s in permission_suggestions if str(s) in _KNOWN_ACTIONS]
        if filtered:
            suggestions = filtered

    buttons = []
    for suggestion in suggestions:
        label = _KNOWN_ACTIONS[suggestion]
        cb = f"{PROMPT_CALLBACK_PREFIX}{progress_msg_id}:{local_id}:{suggestion}"
        buttons.append({"text": label, "callback_data": cb})

    return {"inline_keyboard": [buttons]}


def format_prompt_text(tool_name: str, tool_input: dict) -> str:
    parts = [f"Permission: {tool_name}"]
    if tool_name == "Bash" and "command" in tool_input:
        cmd = str(tool_input["command"])
        if len(cmd) > 200:
            cmd = cmd[:200] + "…"
        parts.append(cmd)
    elif tool_name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path") or tool_input.get("path")
        if path:
            parts.append(str(path))
    elif tool_name == "Glob" and "pattern" in tool_input:
        parts.append(str(tool_input["pattern"]))
    return "\n".join(parts)


class TelegramPresenter:
    def __init__(
        self,
        *,
        formatter: MarkdownFormatter | None = None,
        message_overflow: str = "trim",
    ) -> None:
        self._formatter = formatter or MarkdownFormatter()
        self._message_overflow = message_overflow

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
        if self._message_overflow == "split":
            payloads = prepare_telegram_multi(parts, max_body_chars=MAX_BODY_CHARS)
            text, entities = payloads[0]
            extra = {"entities": entities, "reply_markup": CLEAR_MARKUP}
            if len(payloads) > 1:
                followups = [
                    RenderedMessage(
                        text=followup_text,
                        extra={
                            "entities": followup_entities,
                            "reply_markup": CLEAR_MARKUP,
                        },
                    )
                    for followup_text, followup_entities in payloads[1:]
                ]
                extra["followups"] = followups
            return RenderedMessage(text=text, extra=extra)
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


@dataclass(frozen=True, slots=True)
class TelegramBridgeConfig:
    bot: BotClient
    runtime: TransportRuntime
    chat_id: int
    startup_msg: str
    exec_cfg: ExecBridgeConfig
    session_mode: Literal["stateless", "chat"] = "stateless"
    show_resume_line: bool = True
    voice_transcription: bool = False
    voice_max_bytes: int = 10 * 1024 * 1024
    voice_transcription_model: str = "gpt-4o-mini-transcribe"
    voice_transcription_base_url: str | None = None
    voice_transcription_api_key: str | None = None
    forward_coalesce_s: float = 1.0
    media_group_debounce_s: float = 1.0
    allowed_user_ids: tuple[int, ...] = ()
    files: TelegramFilesSettings = field(default_factory=TelegramFilesSettings)
    chat_ids: tuple[int, ...] | None = None
    topics: TelegramTopicsSettings = field(default_factory=TelegramTopicsSettings)


class TelegramTransport:
    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    @staticmethod
    def _extract_followups(message: RenderedMessage) -> list[RenderedMessage]:
        followups = message.extra.get("followups")
        if not isinstance(followups, list):
            return []
        return [item for item in followups if isinstance(item, RenderedMessage)]

    async def _send_followups(
        self,
        *,
        chat_id: int,
        followups: list[RenderedMessage],
        reply_to_message_id: int | None,
        message_thread_id: int | None,
        notify: bool,
    ) -> None:
        for followup in followups:
            await self._bot.send_message(
                chat_id=chat_id,
                text=followup.text,
                entities=followup.extra.get("entities"),
                parse_mode=followup.extra.get("parse_mode"),
                reply_markup=followup.extra.get("reply_markup"),
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                disable_notification=not notify,
            )

    async def close(self) -> None:
        await self._bot.close()

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        chat_id = cast(int, channel_id)
        reply_to_message_id: int | None = None
        replace_message_id: int | None = None
        message_thread_id: int | None = None
        notify = True
        if options is not None:
            reply_to_message_id = (
                cast(int, options.reply_to.message_id)
                if options.reply_to is not None
                else None
            )
            replace_message_id = (
                cast(int, options.replace.message_id)
                if options.replace is not None
                else None
            )
            notify = options.notify
            message_thread_id = (
                cast(int | None, options.thread_id)
                if options.thread_id is not None
                else None
            )
        else:
            reply_to_message_id = cast(
                int | None,
                message.extra.get("followup_reply_to_message_id"),
            )
            message_thread_id = cast(
                int | None,
                message.extra.get("followup_thread_id"),
            )
            notify = bool(message.extra.get("followup_notify", True))
        followups = self._extract_followups(message)
        sent = await self._bot.send_message(
            chat_id=chat_id,
            text=message.text,
            entities=message.extra.get("entities"),
            parse_mode=message.extra.get("parse_mode"),
            reply_markup=message.extra.get("reply_markup"),
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id,
            replace_message_id=replace_message_id,
            disable_notification=not notify,
        )
        if sent is None:
            return None
        if followups:
            await self._send_followups(
                chat_id=chat_id,
                followups=followups,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                notify=notify,
            )
        message_id = sent.message_id
        thread_id = (
            sent.message_thread_id
            if sent.message_thread_id is not None
            else message_thread_id
        )
        return MessageRef(
            channel_id=chat_id,
            message_id=message_id,
            raw=sent,
            thread_id=thread_id,
        )

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef | None:
        chat_id = cast(int, ref.channel_id)
        message_id = cast(int, ref.message_id)
        entities = message.extra.get("entities")
        parse_mode = message.extra.get("parse_mode")
        reply_markup = message.extra.get("reply_markup")
        followups = self._extract_followups(message)
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
        if followups:
            reply_to_message_id = cast(
                int | None, message.extra.get("followup_reply_to_message_id")
            )
            message_thread_id = cast(
                int | None, message.extra.get("followup_thread_id")
            )
            notify = bool(message.extra.get("followup_notify", True))
            await self._send_followups(
                chat_id=chat_id,
                followups=followups,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
                notify=notify,
            )
        message_id = edited.message_id
        thread_id = (
            edited.message_thread_id
            if edited.message_thread_id is not None
            else ref.thread_id
        )
        return MessageRef(
            channel_id=chat_id,
            message_id=message_id,
            raw=edited,
            thread_id=thread_id,
        )

    async def delete(self, *, ref: MessageRef) -> bool:
        return await self._bot.delete_message(
            chat_id=cast(int, ref.channel_id),
            message_id=cast(int, ref.message_id),
        )


async def send_plain(
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


def build_bot_commands(
    runtime: TransportRuntime,
    *,
    include_file: bool = True,
    include_topics: bool = False,
):
    from .commands import build_bot_commands as _build

    return _build(
        runtime,
        include_file=include_file,
        include_topics=include_topics,
    )


def is_cancel_command(text: str) -> bool:
    from .commands import is_cancel_command as _is_cancel_command

    return _is_cancel_command(text)


async def handle_cancel(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    running_tasks: RunningTasks,
    scheduler: ThreadScheduler | None = None,
) -> None:
    from .commands import handle_cancel as _handle_cancel

    await _handle_cancel(cfg, msg, running_tasks, scheduler)


async def handle_callback_cancel(
    cfg: TelegramBridgeConfig,
    query: TelegramCallbackQuery,
    running_tasks: RunningTasks,
    scheduler: ThreadScheduler | None = None,
) -> None:
    from .commands import handle_callback_cancel as _handle_callback_cancel

    await _handle_callback_cancel(cfg, query, running_tasks, scheduler)


async def handle_callback_prompt(
    cfg: TelegramBridgeConfig,
    query: TelegramCallbackQuery,
    running_tasks: RunningTasks,
) -> None:
    data = query.data or ""
    # Format: takopi:prompt:{progress_msg_id}:{local_id}:{action}
    parts = data[len(PROMPT_CALLBACK_PREFIX) :].split(":", 2)
    if len(parts) != 3:
        await cfg.bot.answer_callback_query(query.callback_query_id, text="invalid")
        return

    progress_msg_id_str, local_id, action = parts
    try:
        progress_msg_id = int(progress_msg_id_str)
    except ValueError:
        await cfg.bot.answer_callback_query(query.callback_query_id, text="invalid")
        return

    ref = MessageRef(channel_id=query.chat_id, message_id=progress_msg_id)
    running_task = running_tasks.get(ref)
    if running_task is None:
        await cfg.bot.answer_callback_query(
            query.callback_query_id, text="session ended"
        )
        return

    if local_id not in running_task.pending_prompts:
        await cfg.bot.answer_callback_query(
            query.callback_query_id, text="expired"
        )
        return

    if action not in {"allow", "deny"}:
        await cfg.bot.answer_callback_query(query.callback_query_id, text="invalid")
        return

    response: dict[str, object] = {}
    if action == "allow":
        response["allowed"] = True
        feedback = "Allowed"
    else:
        response["allowed"] = False
        feedback = "Denied"

    running_task.prompt_responses[local_id] = response
    running_task.pending_prompts[local_id].set()

    await cfg.bot.answer_callback_query(query.callback_query_id, text=feedback)

    # Edit the prompt message to show the decision and remove buttons
    prompt_msg_ref = running_task.prompt_messages.get(local_id)
    if prompt_msg_ref is not None:
        icon = "✓" if action == "allow" else "✗"
        await cfg.bot.edit_message_text(
            chat_id=cast(int, prompt_msg_ref.channel_id),
            message_id=cast(int, prompt_msg_ref.message_id),
            text=f"{icon} {feedback}",
            reply_markup=CLEAR_MARKUP,
        )


async def send_with_resume(
    cfg: TelegramBridgeConfig,
    enqueue: Callable[
        [
            int,
            int,
            str,
            ResumeToken,
            RunContext | None,
            int | None,
            tuple[int, int | None] | None,
            MessageRef | None,
        ],
        Awaitable[None],
    ],
    running_task: RunningTask,
    chat_id: int,
    user_msg_id: int,
    thread_id: int | None,
    session_key: tuple[int, int | None] | None,
    text: str,
) -> None:
    from .loop import send_with_resume as _send_with_resume

    await _send_with_resume(
        cfg,
        enqueue,
        running_task,
        chat_id,
        user_msg_id,
        thread_id,
        session_key,
        text,
    )


async def run_main_loop(
    cfg: TelegramBridgeConfig,
    poller=None,
    *,
    watch_config: bool | None = None,
    default_engine_override: str | None = None,
    transport_id: str | None = None,
    transport_config: TelegramTransportSettings | None = None,
) -> None:
    from .loop import run_main_loop as _run_main_loop

    if poller is None:
        await _run_main_loop(
            cfg,
            watch_config=watch_config,
            default_engine_override=default_engine_override,
            transport_id=transport_id,
            transport_config=transport_config,
        )
    else:
        await _run_main_loop(
            cfg,
            poller=poller,
            watch_config=watch_config,
            default_engine_override=default_engine_override,
            transport_id=transport_id,
            transport_config=transport_config,
        )
