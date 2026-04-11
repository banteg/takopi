from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import anyio

from ..logging import get_logger
from ..model import InteractiveRequest
from ..transport import MessageRef, RenderedMessage, SendOptions, Transport

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

INTERACTIVE_CB_PREFIX = "takopi:interactive"


def make_interactive_markup(
    *,
    chat_id: int,
    msg_id: int,
    request_id: str,
    options: list,
) -> dict:
    buttons = [
        [
            {
                "text": opt.label,
                "callback_data": f"{INTERACTIVE_CB_PREFIX}:{opt.id}:{chat_id}:{msg_id}:{request_id}",
            }
        ]
        for opt in options
    ]
    return {"inline_keyboard": buttons}


class TelegramInteractiveHandler:
    def __init__(self, transport: Transport, chat_id: int) -> None:
        self._transport = transport
        self._chat_id = chat_id
        self._progress_ref: MessageRef | None = None
        self._pending: dict[str, anyio.Event] = {}
        self._responses: dict[str, str] = {}

    def _on_running_task_created(self, running_task: Any, progress_ref: MessageRef) -> None:
        self._progress_ref = progress_ref
        running_task.interactive_handler = self

    def resolve(self, request_id: str, option_id: str) -> None:
        self._responses[request_id] = option_id
        event = self._pending.get(request_id)
        if event is not None:
            event.set()

    async def __call__(self, request: InteractiveRequest) -> str:
        if self._progress_ref is None:
            logger.warning("interactive.no_progress_ref", request_id=request.request_id)
            return "deny"

        event = anyio.Event()
        self._pending[request.request_id] = event
        permission_msg_ref: MessageRef | None = None

        try:
            permission_msg_ref = await self._send_permission_message(request)

            with anyio.move_on_after(60):
                await event.wait()
        finally:
            self._pending.pop(request.request_id, None)

        if permission_msg_ref is not None:
            with contextlib.suppress(Exception):
                await self._transport.delete(ref=permission_msg_ref)

        return self._responses.pop(request.request_id, "deny")

    async def _send_permission_message(
        self, request: InteractiveRequest
    ) -> MessageRef | None:
        assert self._progress_ref is not None
        chat_id = int(self._chat_id)
        msg_id = int(self._progress_ref.message_id)

        lines = [f"Permission: {request.title}"]
        if request.description:
            lines.append(f"`{request.description}`")
        text = "\n".join(lines)

        markup = make_interactive_markup(
            chat_id=chat_id,
            msg_id=msg_id,
            request_id=request.request_id,
            options=request.options,
        )

        rendered = RenderedMessage(text=text, extra={"reply_markup": markup})
        try:
            return await self._transport.send(
                channel_id=self._chat_id,
                message=rendered,
                options=SendOptions(reply_to=self._progress_ref, notify=True),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "interactive.send_failed", request_id=request.request_id
            )
            return None


async def handle_callback_interactive(
    cfg: Any,
    query: Any,
    running_tasks: Any,
) -> None:
    data = query.data or ""
    # Format: takopi:interactive:{opt_id}:{chat_id}:{msg_id}:{request_id}
    parts = data.split(":", 5)
    if len(parts) != 6:
        await cfg.bot.answer_callback_query(query.callback_query_id)
        return

    _, _, opt_id, chat_id_str, msg_id_str, request_id = parts
    try:
        progress_chat_id = int(chat_id_str)
        progress_msg_id = int(msg_id_str)
    except ValueError:
        await cfg.bot.answer_callback_query(query.callback_query_id)
        return

    ref = MessageRef(channel_id=progress_chat_id, message_id=progress_msg_id)
    task = running_tasks.get(ref)
    if task is None or task.interactive_handler is None:
        await cfg.bot.answer_callback_query(
            query.callback_query_id, text="Permission request expired."
        )
        return

    task.interactive_handler.resolve(request_id, opt_id)
    confirmation = "Allowed." if opt_id == "allow" else "Denied."
    await cfg.bot.answer_callback_query(
        query.callback_query_id, text=confirmation
    )
