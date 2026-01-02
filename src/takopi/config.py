from __future__ import annotations

import os
import tomllib
from pathlib import Path

# Environment variable names for secrets
ENV_BOT_TOKEN = "TAKOPI_BOT_TOKEN"
ENV_CHAT_ID = "TAKOPI_CHAT_ID"

LOCAL_CONFIG_NAME = Path(".takopi") / "takopi.toml"
HOME_CONFIG_PATH = Path.home() / ".takopi" / "takopi.toml"
LEGACY_LOCAL_CONFIG_NAME = Path(".codex") / "takopi.toml"
LEGACY_HOME_CONFIG_PATH = Path.home() / ".codex" / "takopi.toml"


class ConfigError(RuntimeError):
    pass


def _config_candidates() -> list[Path]:
    candidates = [Path.cwd() / LOCAL_CONFIG_NAME, HOME_CONFIG_PATH]
    if candidates[0] == candidates[1]:
        return [candidates[0]]
    return candidates


def _legacy_candidates() -> list[Path]:
    candidates = [Path.cwd() / LEGACY_LOCAL_CONFIG_NAME, LEGACY_HOME_CONFIG_PATH]
    if candidates[0] == candidates[1]:
        return [candidates[0]]
    return candidates


def _maybe_migrate_legacy(legacy_path: Path, target_path: Path) -> None:
    if target_path.exists():
        if not target_path.is_file():
            raise ConfigError(
                f"Config path {target_path} exists but is not a file."
            ) from None
        return
    if not legacy_path.is_file():
        return
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        raw = legacy_path.read_text(encoding="utf-8")
        target_path.write_text(raw, encoding="utf-8")
    except OSError as e:
        raise ConfigError(
            f"Failed to migrate legacy config {legacy_path} to {target_path}: {e}"
        ) from e


def _read_config(cfg_path: Path) -> dict:
    try:
        raw = cfg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Missing config file {cfg_path}.") from None
    except OSError as e:
        raise ConfigError(f"Failed to read config file {cfg_path}: {e}") from e
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Malformed TOML in {cfg_path}: {e}") from None


def load_telegram_config(path: str | Path | None = None) -> tuple[dict, Path]:
    if path:
        cfg_path = Path(path).expanduser()
        return _read_config(cfg_path), cfg_path

    for legacy, target in zip(_legacy_candidates(), _config_candidates(), strict=True):
        _maybe_migrate_legacy(legacy, target)

    candidates = _config_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return _read_config(candidate), candidate

    legacy_candidates = _legacy_candidates()
    for candidate in legacy_candidates:
        if candidate.is_file():
            return _read_config(candidate), candidate

    if len(candidates) == 1:
        raise ConfigError("Missing takopi config.")
    raise ConfigError("Missing takopi config.")


def get_bot_token(config: dict, config_path: Path) -> str:
    """Get bot token from environment variable or config file.

    Environment variable TAKOPI_BOT_TOKEN takes precedence over config file.
    """
    # Check environment variable first
    env_token = os.environ.get(ENV_BOT_TOKEN)
    if env_token and env_token.strip():
        return env_token.strip()

    # Fall back to config file
    try:
        token = config["bot_token"]
    except KeyError:
        raise ConfigError(
            f"Missing bot token. Set {ENV_BOT_TOKEN} environment variable "
            f"or add `bot_token` to {config_path}."
        ) from None

    if not isinstance(token, str) or not token.strip():
        raise ConfigError(
            f"Invalid `bot_token` in {config_path}; expected a non-empty string."
        )
    return token.strip()


def get_chat_id(config: dict, config_path: Path) -> int:
    """Get chat ID from environment variable or config file.

    Environment variable TAKOPI_CHAT_ID takes precedence over config file.
    """
    # Check environment variable first
    env_chat_id = os.environ.get(ENV_CHAT_ID)
    if env_chat_id and env_chat_id.strip():
        try:
            return int(env_chat_id.strip())
        except ValueError:
            raise ConfigError(
                f"Invalid {ENV_CHAT_ID} environment variable; expected an integer."
            ) from None

    # Fall back to config file
    try:
        chat_id_value = config["chat_id"]
    except KeyError:
        raise ConfigError(
            f"Missing chat ID. Set {ENV_CHAT_ID} environment variable "
            f"or add `chat_id` to {config_path}."
        ) from None

    if isinstance(chat_id_value, bool) or not isinstance(chat_id_value, int):
        raise ConfigError(
            f"Invalid `chat_id` in {config_path}; expected an integer."
        )
    return chat_id_value
