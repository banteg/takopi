import pytest

from takopi.telegram.api_models import Chat, Message, Update, User
from takopi.telegram.parsing import poll_incoming
from tests.telegram_fakes import FakeBot


class _Bot(FakeBot):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[Update] | None:
        _ = offset, timeout_s, allowed_updates
        self.calls += 1
        if self.calls == 1:
            return None
        return [
            Update(
                update_id=1,
                message=Message(
                    message_id=10,
                    text="hello",
                    chat=Chat(id=123, type="private"),
                    from_=User(id=9),
                ),
            )
        ]


@pytest.mark.anyio
async def test_poll_incoming_retries_on_none() -> None:
    bot = _Bot()
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    msg = None
    async for update in poll_incoming(bot, sleep=sleep):
        msg = update
        break

    assert sleeps == [2]
    assert msg is not None
    assert msg.chat_id == 123
