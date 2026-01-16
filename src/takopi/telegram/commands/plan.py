from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ActionPlan:
    reply_text: str | None
    actions: tuple[Callable[[], Awaitable[None]], ...] = ()

    async def execute(self, reply: Callable[..., Awaitable[None]]) -> None:
        for action in self.actions:
            await action()
        if self.reply_text is not None:
            await reply(text=self.reply_text)
