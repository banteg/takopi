from pathlib import Path

from typer.testing import CliRunner

from takopi import cli
from takopi.settings import TakopiSettings


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
