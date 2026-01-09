from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import TomlConfigSettingsSource

from .config import ConfigError, ProjectConfig, ProjectsConfig, HOME_CONFIG_PATH
from .config_migrations import migrate_config_file


class TelegramTransportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_token: SecretStr | None = None
    chat_id: int | None = None
    voice_transcription: bool = False

    @field_validator("bot_token", mode="before")
    @classmethod
    def _validate_bot_token(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("bot_token must be a string")
        return value

    @field_validator("chat_id", mode="before")
    @classmethod
    def _validate_chat_id(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("chat_id must be an integer")
        return value

    @field_serializer("bot_token")
    def _dump_token(self, value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value else None


class TransportsSettings(BaseModel):
    telegram: TelegramTransportSettings = Field(
        default_factory=TelegramTransportSettings
    )

    model_config = ConfigDict(extra="allow")


class PluginsSettings(BaseModel):
    enabled: list[str] = Field(default_factory=list)
    auto_install: bool = False

    model_config = ConfigDict(extra="allow")


class ProjectSettings(BaseModel):
    path: str
    worktrees_dir: str = ".worktrees"
    default_engine: str | None = None
    worktree_base: str | None = None
    chat_id: int | None = None

    model_config = ConfigDict(extra="allow")

    @field_validator(
        "path",
        "worktrees_dir",
        "default_engine",
        "worktree_base",
        mode="before",
    )
    @classmethod
    def _validate_strings(cls, value: Any, info) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{info.field_name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return cleaned

    @field_validator("chat_id", mode="before")
    @classmethod
    def _validate_chat_id(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("chat_id must be an integer")
        return value


class TakopiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="allow",
        env_prefix="TAKOPI__",
        env_nested_delimiter="__",
    )

    watch_config: bool = False
    default_engine: str = "codex"
    default_project: str | None = None
    projects: dict[str, ProjectSettings] = Field(default_factory=dict)

    transport: str = "telegram"
    transports: TransportsSettings = Field(default_factory=TransportsSettings)

    plugins: PluginsSettings = Field(default_factory=PluginsSettings)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_telegram_keys(cls, data: Any) -> Any:
        if isinstance(data, dict) and ("bot_token" in data or "chat_id" in data):
            raise ValueError(
                "Move bot_token/chat_id under [transports.telegram] "
                'and set transport = "telegram".'
            )
        return data

    @field_validator("default_engine", "transport", mode="before")
    @classmethod
    def _validate_required_strings(cls, value: Any, info) -> Any:
        if value is None:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError(f"{info.field_name} must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return cleaned

    @field_validator("default_project", mode="before")
    @classmethod
    def _validate_default_project(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("default_project must be a string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("default_project must be a non-empty string")
        return cleaned

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    def engine_config(self, engine_id: str, *, config_path: Path) -> dict[str, Any]:
        extra = self.model_extra or {}
        raw = extra.get(engine_id)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ConfigError(
                f"Invalid `{engine_id}` config in {config_path}; expected a table."
            )
        return raw

    def transport_config(
        self, transport_id: str, *, config_path: Path
    ) -> dict[str, Any]:
        if transport_id == "telegram":
            return self.transports.telegram.model_dump()
        extra = self.transports.model_extra or {}
        raw = extra.get(transport_id)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ConfigError(
                f"Invalid `transports.{transport_id}` in {config_path}; "
                "expected a table."
            )
        return raw

    def to_projects_config(
        self,
        *,
        config_path: Path,
        engine_ids: Iterable[str],
        reserved: Iterable[str] = ("cancel",),
    ) -> ProjectsConfig:
        default_project = self.default_project
        default_chat_id = self.transports.telegram.chat_id

        reserved_lower = {value.lower() for value in reserved}
        engine_map = {engine.lower(): engine for engine in engine_ids}
        projects: dict[str, ProjectConfig] = {}
        chat_map: dict[int, str] = {}

        for raw_alias, entry in self.projects.items():
            if not isinstance(raw_alias, str) or not raw_alias.strip():
                raise ConfigError(
                    f"Invalid project alias in {config_path}; expected a non-empty string."
                )
            alias = raw_alias.strip()
            alias_key = alias.lower()
            if alias_key in engine_map or alias_key in reserved_lower:
                raise ConfigError(
                    f"Invalid project alias {alias!r} in {config_path}; "
                    "aliases must not match engine ids or reserved commands."
                )
            if alias_key in projects:
                raise ConfigError(
                    f"Duplicate project alias {alias!r} in {config_path}."
                )

            path_value = entry.path
            if not isinstance(path_value, str) or not path_value.strip():
                raise ConfigError(
                    f"Missing `path` for project {alias!r} in {config_path}."
                )
            path = _normalize_project_path(path_value.strip(), config_path=config_path)

            worktrees_dir_raw = entry.worktrees_dir
            if not isinstance(worktrees_dir_raw, str) or not worktrees_dir_raw.strip():
                raise ConfigError(
                    f"Invalid `worktrees_dir` for project {alias!r} in {config_path}."
                )
            worktrees_dir = Path(worktrees_dir_raw.strip())

            default_engine_raw = entry.default_engine
            default_engine = None
            if default_engine_raw is not None:
                if not isinstance(default_engine_raw, str):
                    raise ConfigError(
                        f"Invalid `projects.{alias}.default_engine` in {config_path}; "
                        "expected a string."
                    )
                default_engine = _normalize_engine_id(
                    default_engine_raw,
                    engine_ids=engine_ids,
                    config_path=config_path,
                    label=f"projects.{alias}.default_engine",
                )

            worktree_base_raw = entry.worktree_base
            worktree_base = None
            if worktree_base_raw is not None:
                if (
                    not isinstance(worktree_base_raw, str)
                    or not worktree_base_raw.strip()
                ):
                    raise ConfigError(
                        f"Invalid `projects.{alias}.worktree_base` in {config_path}; "
                        "expected a string."
                    )
                worktree_base = worktree_base_raw.strip()

            chat_id = entry.chat_id
            if chat_id is not None:
                if isinstance(chat_id, bool) or not isinstance(chat_id, int):
                    raise ConfigError(
                        f"Invalid `projects.{alias}.chat_id` in {config_path}; "
                        "expected an integer."
                    )
                if default_chat_id is not None and chat_id == default_chat_id:
                    raise ConfigError(
                        f"Invalid `projects.{alias}.chat_id` in {config_path}; "
                        "must not match transports.telegram.chat_id."
                    )
                if chat_id in chat_map:
                    existing = chat_map[chat_id]
                    raise ConfigError(
                        f"Duplicate `projects.*.chat_id` {chat_id} in {config_path}; "
                        f"already used by {existing!r}."
                    )
                chat_map[chat_id] = alias_key

            projects[alias_key] = ProjectConfig(
                alias=alias,
                path=path,
                worktrees_dir=worktrees_dir,
                default_engine=default_engine,
                worktree_base=worktree_base,
                chat_id=chat_id,
            )

        if default_project is not None:
            default_key = default_project.lower()
            if default_key not in projects:
                raise ConfigError(
                    f"Invalid `default_project` {default_project!r} in {config_path}; "
                    "no matching project alias found."
                )
            default_project = default_key

        return ProjectsConfig(
            projects=projects,
            default_project=default_project,
            chat_map=chat_map,
        )


def load_settings(path: str | Path | None = None) -> tuple[TakopiSettings, Path]:
    cfg_path = _resolve_config_path(path)
    _ensure_config_file(cfg_path)
    migrate_config_file(cfg_path)
    return _load_settings_from_path(cfg_path), cfg_path


def load_settings_if_exists(
    path: str | Path | None = None,
) -> tuple[TakopiSettings, Path] | None:
    cfg_path = _resolve_config_path(path)
    if cfg_path.exists():
        if not cfg_path.is_file():
            raise ConfigError(
                f"Config path {cfg_path} exists but is not a file."
            ) from None
        migrate_config_file(cfg_path)
        return _load_settings_from_path(cfg_path), cfg_path
    return None


def validate_settings_data(
    data: dict[str, Any], *, config_path: Path
) -> TakopiSettings:
    try:
        return TakopiSettings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {config_path}: {exc}") from exc


def require_telegram(settings: TakopiSettings, config_path: Path) -> tuple[str, int]:
    if settings.transport != "telegram":
        raise ConfigError(
            f"Unsupported transport {settings.transport!r} in {config_path} "
            "(telegram only for now)."
        )
    tg = settings.transports.telegram
    if tg.bot_token is None or not tg.bot_token.get_secret_value().strip():
        raise ConfigError(f"Missing bot token in {config_path}.")
    if tg.chat_id is None:
        raise ConfigError(f"Missing chat_id in {config_path}.")
    if isinstance(tg.chat_id, bool) or not isinstance(tg.chat_id, int):
        raise ConfigError(f"Invalid `chat_id` in {config_path}; expected an integer.")
    return tg.bot_token.get_secret_value().strip(), tg.chat_id


def require_telegram_config(
    config: dict[str, object], config_path: Path
) -> tuple[str, int]:
    raw_token = config.get("bot_token")
    if raw_token is None or not isinstance(raw_token, str) or not raw_token.strip():
        raise ConfigError(f"Missing bot token in {config_path}.")
    raw_chat_id = config.get("chat_id")
    if raw_chat_id is None:
        raise ConfigError(f"Missing chat_id in {config_path}.")
    if isinstance(raw_chat_id, bool) or not isinstance(raw_chat_id, int):
        raise ConfigError(f"Invalid `chat_id` in {config_path}; expected an integer.")
    return raw_token.strip(), raw_chat_id


def _resolve_config_path(path: str | Path | None) -> Path:
    return Path(path).expanduser() if path else HOME_CONFIG_PATH


def _ensure_config_file(cfg_path: Path) -> None:
    if cfg_path.exists() and not cfg_path.is_file():
        raise ConfigError(f"Config path {cfg_path} exists but is not a file.") from None
    if not cfg_path.exists():
        raise ConfigError(f"Missing config file {cfg_path}.") from None


def _load_settings_from_path(cfg_path: Path) -> TakopiSettings:
    cfg = dict(TakopiSettings.model_config)
    cfg["toml_file"] = cfg_path
    Bound = type(
        "TakopiSettingsBound",
        (TakopiSettings,),
        {"model_config": SettingsConfigDict(**cfg)},
    )
    try:
        return Bound()
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {cfg_path}: {exc}") from exc
    except Exception as exc:  # pragma: no cover - safety net
        raise ConfigError(f"Failed to load config {cfg_path}: {exc}") from exc


def _normalize_engine_id(
    value: str,
    *,
    engine_ids: Iterable[str],
    config_path: Path,
    label: str,
) -> str:
    engine_map = {engine.lower(): engine for engine in engine_ids}
    cleaned = value.strip()
    if not cleaned:
        raise ConfigError(f"Invalid `{label}` in {config_path}; expected a string.")
    engine = engine_map.get(cleaned.lower())
    if engine is None:
        available = ", ".join(sorted(engine_map.values()))
        raise ConfigError(
            f"Unknown `{label}` {cleaned!r} in {config_path}. Available: {available}."
        )
    return engine


def _normalize_project_path(value: str, *, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path
