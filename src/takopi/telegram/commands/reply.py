from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING

from ..bridge import send_plain
from ..types import TelegramIncomingMessage

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig


def make_reply(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage | None,
    *,
    chat_id: int | None = None,
    user_msg_id: int | None = None,
    thread_id: int | None = None,
) -> Callable[..., Awaitable[None]]:
    if msg is not None:
        return partial(
            send_plain,
            cfg.exec_cfg.transport,
            chat_id=msg.chat_id,
            user_msg_id=msg.message_id,
            thread_id=msg.thread_id,
        )
    # Fallback when msg is None - use explicit parameters
    return partial(
        send_plain,
        cfg.exec_cfg.transport,
        chat_id=chat_id or 0,
        user_msg_id=user_msg_id or 0,
        thread_id=thread_id,
    )
