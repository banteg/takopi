from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

ChannelId: TypeAlias = int | str
MessageId: TypeAlias = int | str


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    transport: str
    chat_id: int
    message_id: int
    text: str
    reply_to_message_id: int | None
    reply_to_text: str | None
    sender_id: int | None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MessageRef:
    channel_id: ChannelId
    message_id: MessageId
    raw: Any | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendOptions:
    reply_to: MessageRef | None = None
    notify: bool = True
    replace: MessageRef | None = None


class Transport(Protocol):
    async def close(self) -> None: ...

    async def send(
        self,
        *,
        channel_id: ChannelId,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None: ...

    async def edit(
        self,
        *,
        ref: MessageRef,
        message: RenderedMessage,
        wait: bool = True,
    ) -> MessageRef | None: ...

    async def delete(self, *, ref: MessageRef) -> bool: ...
