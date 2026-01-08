from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ConfigError
from .config_store import read_raw_toml, write_raw_toml
from .logging import get_logger

logger = get_logger(__name__)


def _ensure_table(
    config: dict[str, Any],
    key: str,
    *,
    config_path: Path,
    label: str | None = None,
) -> dict[str, Any]:
    value = config.get(key)
    if value is None:
        table: dict[str, Any] = {}
        config[key] = table
        return table
    if not isinstance(value, dict):
        name = label or key
        raise ConfigError(f"Invalid `{name}` in {config_path}; expected a table.")
    return value


def _migrate_legacy_telegram(config: dict[str, Any], *, config_path: Path) -> bool:
    has_legacy = "bot_token" in config or "chat_id" in config
    if not has_legacy:
        return False

    transports = _ensure_table(config, "transports", config_path=config_path)
    telegram = transports.get("telegram")
    if telegram is None:
        telegram = {}
        transports["telegram"] = telegram
    if not isinstance(telegram, dict):
        raise ConfigError(
            f"Invalid `transports.telegram` in {config_path}; expected a table."
        )

    if "bot_token" in config and "bot_token" not in telegram:
        telegram["bot_token"] = config["bot_token"]
    if "chat_id" in config and "chat_id" not in telegram:
        telegram["chat_id"] = config["chat_id"]

    config.pop("bot_token", None)
    config.pop("chat_id", None)
    config.setdefault("transport", "telegram")
    return True


def migrate_config(config: dict[str, Any], *, config_path: Path) -> list[str]:
    applied: list[str] = []
    if _migrate_legacy_telegram(config, config_path=config_path):
        applied.append("legacy-telegram")
    return applied


def migrate_config_file(path: Path) -> list[str]:
    config = read_raw_toml(path)
    applied = migrate_config(config, config_path=path)
    if applied:
        write_raw_toml(config, path)
        for migration in applied:
            logger.info(
                "config.migrated",
                migration=migration,
                path=str(path),
            )
    return applied
