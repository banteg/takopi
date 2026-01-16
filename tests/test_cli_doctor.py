from pathlib import Path

import pytest
from typer.testing import CliRunner

from takopi import cli
from takopi.config import ConfigError
from takopi.settings import TakopiSettings
from takopi.settings import TelegramTopicsSettings
from takopi.telegram.api_models import Chat, User


def _settings() -> TakopiSettings:
    return TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )


def test_doctor_ok(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(cli, "load_settings", lambda: (settings, Path("x")))
    monkeypatch.setattr(cli, "resolve_plugins_allowlist", lambda _settings: None)
    monkeypatch.setattr(cli, "list_backend_ids", lambda allowlist=None: ["codex"])

    async def _fake_checks(*_args, **_kwargs):
        return [cli.DoctorCheck("telegram token", "ok", "@bot")]

    monkeypatch.setattr(cli, "_doctor_telegram_checks", _fake_checks)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["doctor"])

    assert result.exit_code == 0
    assert "takopi doctor" in result.output
    assert "telegram token: ok" in result.output


def test_doctor_errors_exit_nonzero(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(cli, "load_settings", lambda: (settings, Path("x")))
    monkeypatch.setattr(cli, "resolve_plugins_allowlist", lambda _settings: None)
    monkeypatch.setattr(cli, "list_backend_ids", lambda allowlist=None: ["codex"])

    async def _fake_checks(*_args, **_kwargs):
        return [cli.DoctorCheck("telegram token", "error", "bad token")]

    monkeypatch.setattr(cli, "_doctor_telegram_checks", _fake_checks)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["doctor"])

    assert result.exit_code == 1
    assert "telegram token: error" in result.output


class _FakeBot:
    def __init__(self, me: User | None, chat: Chat | None) -> None:
        self._me = me
        self._chat = chat
        self.closed = False

    async def get_me(self) -> User | None:
        return self._me

    async def get_chat(self, chat_id: int) -> Chat | None:
        _ = chat_id
        return self._chat

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_doctor_telegram_checks_invalid_token(monkeypatch) -> None:
    bot = _FakeBot(me=None, chat=None)
    monkeypatch.setattr(cli, "TelegramClient", lambda _token: bot)
    topics = TelegramTopicsSettings(enabled=True)

    checks = await cli._doctor_telegram_checks(
        "token",
        123,
        topics,
        (),
    )

    assert [check.label for check in checks] == [
        "telegram token",
        "chat_id",
        "topics",
    ]
    assert checks[0].status == "error"
    assert checks[1].detail == "skipped (token invalid)"
    assert checks[2].detail == "skipped (token invalid)"
    assert bot.closed is True


@pytest.mark.anyio
async def test_doctor_telegram_checks_chat_and_topics_error(monkeypatch) -> None:
    bot = _FakeBot(
        me=User(id=1, username="bot", first_name=None, last_name=None),
        chat=None,
    )
    monkeypatch.setattr(cli, "TelegramClient", lambda _token: bot)

    async def _raise_topics(*_args, **_kwargs) -> None:
        raise ConfigError("bad topics")

    monkeypatch.setattr(cli, "_validate_topics_setup_for", _raise_topics)
    topics = TelegramTopicsSettings(enabled=True)

    checks = await cli._doctor_telegram_checks(
        "token",
        321,
        topics,
        (),
    )

    assert checks[0].detail == "@bot"
    assert checks[1].status == "error"
    assert "unreachable" in (checks[1].detail or "")
    assert checks[2].detail == "bad topics"
    assert bot.closed is True
