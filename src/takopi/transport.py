from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

type ChannelId = int | str
type MessageId = int | str
type ThreadId = int | str


@dataclass(frozen=True, slots=True)
class MessageRef:
    channel_id: ChannelId
    message_id: MessageId
    raw: Any | None = field(default=None, compare=False, hash=False)
    thread_id: ThreadId | None = field(default=None, compare=False, hash=False)
    sender_id: int | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendOptions:
    reply_to: MessageRef | None = None
    notify: bool = True
    replace: MessageRef | None = None
    thread_id: ThreadId | None = None


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
