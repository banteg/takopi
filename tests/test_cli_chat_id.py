from pathlib import Path

from typer.testing import CliRunner

from takopi import cli
from takopi.settings import TakopiSettings
from takopi.telegram import onboarding


def test_chat_id_command_updates_project_chat_id(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        '[projects.z80]\npath = "/tmp/repo"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("takopi.config.HOME_CONFIG_PATH", config_path)
    monkeypatch.setattr(cli, "_load_settings_optional", lambda: (None, None))

    async def _capture(*, token: str | None = None):
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
    saved = config_path.read_text(encoding="utf-8")
    assert "chat_id = 123" in saved
    assert "updated projects.z80.chat_id = 123" in result.output


def test_chat_id_command_uses_config_token(monkeypatch) -> None:
    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "config-token", "chat_id": 123}},
        }
    )
    monkeypatch.setattr(cli, "_load_settings_optional", lambda: (settings, Path("x")))

    async def _capture(*, token: str | None = None):
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
    assert "chat_id = 321" in result.output
