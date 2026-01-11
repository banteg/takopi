from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ConfigError, ensure_table, read_config, write_config
from .logging import get_logger

logger = get_logger(__name__)


def _ensure_subtable(
    parent: dict[str, Any],
    key: str,
    *,
    config_path: Path,
    label: str,
) -> dict[str, Any] | None:
    value = parent.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError(f"Invalid `{label}` in {config_path}; expected a table.")
    return value


def _migrate_legacy_telegram(config: dict[str, Any], *, config_path: Path) -> bool:
    has_legacy = "bot_token" in config or "chat_id" in config
    if not has_legacy:
        return False

    transports = ensure_table(config, "transports", config_path=config_path)
    telegram = ensure_table(
        transports,
        "telegram",
        config_path=config_path,
        label="transports.telegram",
    )

    if "bot_token" in config and "bot_token" not in telegram:
        telegram["bot_token"] = config["bot_token"]
    if "chat_id" in config and "chat_id" not in telegram:
        telegram["chat_id"] = config["chat_id"]

    config.pop("bot_token", None)
    config.pop("chat_id", None)
    config.setdefault("transport", "telegram")
    return True


def _migrate_topics_scope(config: dict[str, Any], *, config_path: Path) -> bool:
    transports = _ensure_subtable(
        config,
        "transports",
        config_path=config_path,
        label="transports",
    )
    if transports is None:
        return False

    telegram = _ensure_subtable(
        transports,
        "telegram",
        config_path=config_path,
        label="transports.telegram",
    )
    if telegram is None:
        return False

    topics = _ensure_subtable(
        telegram,
        "topics",
        config_path=config_path,
        label="transports.telegram.topics",
    )
    if topics is None:
        return False
    if "mode" not in topics:
        return False

    if "scope" not in topics:
        mode = topics.get("mode")
        if not isinstance(mode, str):
            raise ConfigError(
                f"Invalid `transports.telegram.topics.mode` in {config_path}; "
                "expected a string."
            )
        cleaned = mode.strip()
        mapping = {
            "multi_project_chat": "main",
            "per_project_chat": "projects",
        }
        if cleaned not in mapping:
            raise ConfigError(
                f"Invalid `transports.telegram.topics.mode` in {config_path}; "
                "expected 'multi_project_chat' or 'per_project_chat'."
            )
        topics["scope"] = mapping[cleaned]

    topics.pop("mode", None)
    return True


def migrate_config(config: dict[str, Any], *, config_path: Path) -> list[str]:
    applied: list[str] = []
    if _migrate_legacy_telegram(config, config_path=config_path):
        applied.append("legacy-telegram")
    if _migrate_topics_scope(config, config_path=config_path):
        applied.append("topics-scope")
    return applied


def migrate_config_file(path: Path) -> list[str]:
    config = read_config(path)
    applied = migrate_config(config, config_path=path)
    if applied:
        write_config(config, path)
        for migration in applied:
            logger.info(
                "config.migrated",
                migration=migration,
                path=str(path),
            )
    return applied
