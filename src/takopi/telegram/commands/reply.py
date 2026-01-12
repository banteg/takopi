from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING

from ..bridge import send_plain
from ..types import TelegramIncomingMessage

if TYPE_CHECKING:
    from ..bridge import TelegramBridgeConfig


def make_reply(
    cfg: TelegramBridgeConfig, msg: TelegramIncomingMessage
) -> Callable[..., Awaitable[None]]:
    return partial(
        send_plain,
        cfg.exec_cfg.transport,
        chat_id=msg.chat_id,
        user_msg_id=msg.message_id,
        thread_id=msg.thread_id,
    )
