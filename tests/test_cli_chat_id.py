from pathlib import Path

from typer.testing import CliRunner

from takopi import cli
from takopi.settings import TakopiSettings
from takopi.telegram import onboarding


def test_chat_id_command_uses_token_option(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_load_settings_optional", lambda: (None, None))

    def _capture(*, token: str | None = None):
        assert token == "token"
        return onboarding.ChatInfo(
            chat_id=123,
            username=None,
            title="takopi",
            first_name=None,
            last_name=None,
            chat_type="supergroup",
        )

    monkeypatch.setattr(cli.onboarding, "capture_chat_id", _capture)

    runner = CliRunner()
    result = runner.invoke(
        cli.create_app(),
        ["chat-id", "--token", "token", "--project", "z80"],
    )

    assert result.exit_code == 0
    assert "projects.z80.chat_id = 123" in result.output


def test_chat_id_command_uses_config_token(monkeypatch) -> None:
    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "config-token"}},
        }
    )
    monkeypatch.setattr(cli, "_load_settings_optional", lambda: (settings, Path("x")))

    def _capture(*, token: str | None = None):
        assert token == "config-token"
        return onboarding.ChatInfo(
            chat_id=321,
            username=None,
            title="takopi",
            first_name=None,
            last_name=None,
            chat_type="supergroup",
        )

    monkeypatch.setattr(cli.onboarding, "capture_chat_id", _capture)

    runner = CliRunner()
    result = runner.invoke(cli.create_app(), ["chat-id"])

    assert result.exit_code == 0
