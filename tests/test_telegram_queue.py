from typing import Any

import anyio
import pytest

from takopi.telegram.api_models import Chat, File, Message, Update, User
from takopi.telegram.client import BotClient, TelegramClient, TelegramRetryAfter


class FakeBot(BotClient):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.edit_calls: list[str] = []
        self.delete_calls: list[tuple[int, int]] = []
        self.topic_calls: list[tuple[int, int, str]] = []
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
        message_thread_id: int | None = None,
        entities: list[dict[str, Any]] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> Message | None:
        _ = reply_to_message_id
        _ = disable_notification
        _ = message_thread_id
        _ = entities
        _ = parse_mode
        _ = reply_markup
        _ = replace_message_id
        self.calls.append("send_message")
        return Message(message_id=1, chat=Chat(id=chat_id, type="private"))

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool | None = False,
        caption: str | None = None,
    ) -> Message | None:
        _ = (
            chat_id,
            filename,
            content,
            reply_to_message_id,
            message_thread_id,
            disable_notification,
            caption,
        )
        self.calls.append("send_document")
        return Message(message_id=1, chat=Chat(id=chat_id, type="private"))

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict[str, Any]] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        *,
        wait: bool = True,
    ) -> Message | None:
        _ = chat_id
        _ = message_id
        _ = entities
        _ = parse_mode
        _ = reply_markup
        _ = wait
        self.calls.append("edit_message_text")
        self.edit_calls.append(text)
        if self.retry_after is not None and self._edit_attempts == 0:
            self._edit_attempts += 1
            raise TelegramRetryAfter(self.retry_after)
        self._edit_attempts += 1
        return Message(message_id=message_id, chat=Chat(id=chat_id, type="private"))

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
    ) -> bool:
        self.calls.append("delete_message")
        self.delete_calls.append((chat_id, message_id))
        return True

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        _ = commands
        _ = scope
        _ = language_code
        return True

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[Update] | None:
        _ = offset
        _ = timeout_s
        _ = allowed_updates
        if self.updates_retry_after is not None and self._updates_attempts == 0:
            self._updates_attempts += 1
            raise TelegramRetryAfter(self.updates_retry_after)
        self._updates_attempts += 1
        return []

    async def get_file(self, file_id: str) -> File | None:
        _ = file_id
        return None

    async def download_file(self, file_path: str) -> bytes | None:
        _ = file_path
        return None

    async def close(self) -> None:
        return None

    async def get_me(self) -> User | None:
        return User(id=1)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool:
        _ = callback_query_id, text, show_alert
        return True

    async def edit_forum_topic(
        self, chat_id: int, message_thread_id: int, name: str
    ) -> bool:
        self.calls.append("edit_forum_topic")
        self.topic_calls.append((chat_id, message_thread_id, name))
        return True


@pytest.mark.anyio
async def test_edit_forum_topic_uses_outbox() -> None:
    bot = FakeBot()
    client = TelegramClient(client=bot, private_chat_rps=0.0, group_chat_rps=0.0)

    result = await client.edit_forum_topic(
        chat_id=7, message_thread_id=42, name="takopi @main"
    )

    assert result is True
    assert bot.calls == ["edit_forum_topic"]
    assert bot.topic_calls == [(7, 42, "takopi @main")]


@pytest.mark.anyio
async def test_edits_coalesce_latest() -> None:
    class _BlockingBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.edit_started = anyio.Event()
            self.release = anyio.Event()
            self._block_first = True

        async def edit_message_text(
            self,
            chat_id: int,
            message_id: int,
            text: str,
            entities: list[dict] | None = None,
            parse_mode: str | None = None,
            reply_markup: dict | None = None,
            *,
            wait: bool = True,
        ) -> Message | None:
            if self._block_first:
                self._block_first = False
                self.edit_started.set()
                await self.release.wait()
            return await super().edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                entities=entities,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                wait=wait,
            )

    bot = _BlockingBot()
    client = TelegramClient(client=bot, private_chat_rps=0.0, group_chat_rps=0.0)

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="first",
        wait=False,
    )

    with anyio.fail_after(1):
        await bot.edit_started.wait()

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="second",
        wait=False,
    )
    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="third",
        wait=False,
    )

    bot.release.set()

    with anyio.fail_after(1):
        while len(bot.edit_calls) < 2:
            await anyio.sleep(0)

    assert bot.edit_calls == ["first", "third"]


@pytest.mark.anyio
async def test_send_preempts_pending_edit() -> None:
    bot = FakeBot()
    client = TelegramClient(client=bot, private_chat_rps=0.0, group_chat_rps=0.0)

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="first",
    )

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="progress",
        wait=False,
    )

    with anyio.fail_after(1):
        await client.send_message(chat_id=1, text="final")

    with anyio.fail_after(1):
        while len(bot.calls) < 3:
            await anyio.sleep(0)
    assert bot.calls[0] == "edit_message_text"
    assert bot.calls[1] == "send_message"
    assert bot.calls[-1] == "edit_message_text"


@pytest.mark.anyio
async def test_delete_drops_pending_edits() -> None:
    bot = FakeBot()
    client = TelegramClient(client=bot, private_chat_rps=0.0, group_chat_rps=0.0)

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="first",
    )

    await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="progress",
        wait=False,
    )

    with anyio.fail_after(1):
        await client.delete_message(
            chat_id=1,
            message_id=1,
        )

    with anyio.fail_after(1):
        while not bot.delete_calls:
            await anyio.sleep(0)
    assert bot.delete_calls == [(1, 1)]
    assert bot.edit_calls == ["first"]


@pytest.mark.anyio
async def test_retry_after_retries_once() -> None:
    bot = FakeBot()
    bot.retry_after = 0.0
    client = TelegramClient(client=bot, private_chat_rps=0.0, group_chat_rps=0.0)

    result = await client.edit_message_text(
        chat_id=1,
        message_id=1,
        text="retry",
    )

    assert result is not None
    assert result.message_id == 1
    assert bot._edit_attempts == 2


@pytest.mark.anyio
async def test_get_updates_retries_on_retry_after() -> None:
    bot = FakeBot()
    bot.updates_retry_after = 0.0
    client = TelegramClient(client=bot, private_chat_rps=0.0, group_chat_rps=0.0)

    with anyio.fail_after(1):
        updates = await client.get_updates(offset=None, timeout_s=0)

    assert updates == []
    assert bot._updates_attempts == 2
