from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

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
        shutil.move(legacy_path, target_path)
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

    config_candidates = _config_candidates()
    legacy_candidates = _legacy_candidates()
    for legacy, target in zip(legacy_candidates, config_candidates, strict=True):
        _maybe_migrate_legacy(legacy, target)

    for candidate in config_candidates:
        if candidate.is_file():
            return _read_config(candidate), candidate

    for candidate in legacy_candidates:
        if candidate.is_file():
            return _read_config(candidate), candidate

    checked: list[Path] = []
    for candidate in [*config_candidates, *legacy_candidates]:
        if candidate in checked:
            continue
        checked.append(candidate)
    checked_display = ", ".join(str(candidate) for candidate in checked)
    raise ConfigError(f"Missing takopi config. Checked: {checked_display}")
