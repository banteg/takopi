import time

import anyio
import pytest

from takopi.telegram import QueuedTelegramClient, TelegramPriority, TelegramRetryAfter


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.edit_calls: list[str] = []
        self.delete_calls: list[tuple[int, int]] = []
        self._edit_attempts = 0
        self._updates_attempts = 0
        self.retry_after: float | None = None
        self.updates_retry_after: float | None = None

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
    ) -> dict:
        _ = reply_to_message_id
        _ = disable_notification
        _ = entities
        _ = parse_mode
        _ = priority
        _ = not_before
        self.calls.append("send_message")
        return {"message_id": 1}

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict] | None = None,
        parse_mode: str | None = None,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        not_before: float | None = None,
        wait: bool = True,
    ) -> dict:
        _ = chat_id
        _ = message_id
        _ = entities
        _ = parse_mode
        _ = priority
        _ = not_before
        _ = wait
        self.calls.append("edit_message_text")
        self.edit_calls.append(text)
        if self.retry_after is not None and self._edit_attempts == 0:
            self._edit_attempts += 1
            raise TelegramRetryAfter(self.retry_after)
        self._edit_attempts += 1
        return {"message_id": message_id}

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
    ) -> bool:
        _ = priority
        self.calls.append("delete_message")
        self.delete_calls.append((chat_id, message_id))
        return True

    async def set_my_commands(
        self,
        commands: list[dict],
        *,
        priority: TelegramPriority = TelegramPriority.HIGH,
        scope: dict | None = None,
        language_code: str | None = None,
    ) -> bool:
        _ = commands
        _ = priority
        _ = scope
        _ = language_code
        return True

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[dict] | None:
        _ = offset
        _ = timeout_s
        _ = allowed_updates
        if self.updates_retry_after is not None and self._updates_attempts == 0:
            self._updates_attempts += 1
            raise TelegramRetryAfter(self.updates_retry_after)
        self._updates_attempts += 1
        return []

    async def close(self) -> None:
        return None

    async def get_me(
        self, *, priority: TelegramPriority = TelegramPriority.HIGH
    ) -> dict | None:
        _ = priority
        return {"id": 1}


@pytest.mark.anyio
async def test_low_edits_coalesce_latest() -> None:
    bot = _FakeBot()
    client = QueuedTelegramClient(bot, private_chat_rps=0.0, group_chat_rps=0.0)
    not_before = time.monotonic() + 0.2

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="first",
        priority=TelegramPriority.LOW,
        not_before=not_before,
        wait=False,
    )
    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="second",
        priority=TelegramPriority.LOW,
        not_before=not_before,
        wait=False,
    )

    with anyio.fail_after(1):
        await client.edit_message_text(
            chat_id=1,
            message_id=1,
            text="third",
            priority=TelegramPriority.LOW,
            not_before=not_before,
        )

    assert bot.edit_calls == ["third"]


@pytest.mark.anyio
async def test_high_priority_preempts_low() -> None:
    bot = _FakeBot()
    client = QueuedTelegramClient(bot, private_chat_rps=0.0, group_chat_rps=0.0)
    not_before = time.monotonic() + 0.2

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="progress",
        priority=TelegramPriority.LOW,
        not_before=not_before,
        wait=False,
    )

    with anyio.fail_after(1):
        await client.send_message(
            chat_id=1,
            text="final",
            priority=TelegramPriority.HIGH,
        )

    await anyio.sleep(0.25)
    assert bot.calls[0] == "send_message"
    assert bot.calls[-1] == "edit_message_text"


@pytest.mark.anyio
async def test_delete_drops_pending_edits() -> None:
    bot = _FakeBot()
    client = QueuedTelegramClient(bot, private_chat_rps=0.0, group_chat_rps=0.0)
    not_before = time.monotonic() + 0.2

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="progress",
        priority=TelegramPriority.LOW,
        not_before=not_before,
        wait=False,
    )

    with anyio.fail_after(1):
        await client.delete_message(
            chat_id=1,
            message_id=1,
            priority=TelegramPriority.HIGH,
        )

    await anyio.sleep(0.25)
    assert bot.delete_calls == [(1, 1)]
    assert bot.edit_calls == []


@pytest.mark.anyio
async def test_retry_after_retries_once() -> None:
    bot = _FakeBot()
    bot.retry_after = 0.01
    sleep_calls: list[float] = []

    async def sleep(delay: float) -> None:
        sleep_calls.append(delay)
        await anyio.sleep(0)

    client = QueuedTelegramClient(
        bot,
        sleep=sleep,
        private_chat_rps=0.0,
        group_chat_rps=0.0,
    )

    result = await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="retry",
        priority=TelegramPriority.HIGH,
    )

    assert result == {"message_id": 1}
    assert bot._edit_attempts == 2
    assert sleep_calls == [0.01]


@pytest.mark.anyio
async def test_get_updates_retries_on_retry_after() -> None:
    bot = _FakeBot()
    bot.updates_retry_after = 0.0
    client = QueuedTelegramClient(bot, private_chat_rps=0.0, group_chat_rps=0.0)

    with anyio.fail_after(1):
        updates = await client.get_updates(offset=None, timeout_s=0)

    assert updates == []
    assert bot._updates_attempts == 2
