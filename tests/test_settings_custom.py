from pathlib import Path
from takopi.settings import load_settings

def test_telegram_transport_settings_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n'
        '[transports.telegram]\n'
        'bot_token = "token"\n'
        'chat_id = 123\n',
        encoding="utf-8",
    )
    settings, _ = load_settings(config_path)
    tg_settings = settings.transports.telegram
    assert tg_settings.progress_updates == "full"
    assert tg_settings.show_typing is False

def test_telegram_transport_settings_custom(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n'
        '[transports.telegram]\n'
        'bot_token = "token"\n'
        'chat_id = 123\n'
        'progress_updates = "once"\n'
        'show_typing = true\n',
        encoding="utf-8",
    )
    settings, _ = load_settings(config_path)
    tg_settings = settings.transports.telegram
    assert tg_settings.progress_updates == "once"
    assert tg_settings.show_typing is True
