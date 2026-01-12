from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Iterable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.types import StrictInt
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import TomlConfigSettingsSource

from .config import (
    ConfigError,
    HOME_CONFIG_PATH,
    ProjectConfig,
    ProjectsConfig,
)
from .config_migrations import migrate_config_file


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _normalize_engine_id(
    value: str,
    *,
    engine_ids: Iterable[str],
    config_path: Path,
    label: str,
) -> str:
    engine_map = {engine.lower(): engine for engine in engine_ids}
    engine = engine_map.get(value.lower())
    if engine is None:
        available = ", ".join(sorted(engine_map.values()))
        raise ConfigError(
            f"Unknown `{label}` {value!r} in {config_path}. Available: {available}."
        )
    return engine


def _normalize_project_path(value: str, *, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path


class TelegramTopicsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = False
    scope: Literal["auto", "main", "projects", "all"] = "auto"


class TelegramFilesSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = False
    auto_put: bool = True
    uploads_dir: NonEmptyStr = "incoming"
    allowed_user_ids: list[StrictInt] = Field(default_factory=list)
    deny_globs: list[NonEmptyStr] = Field(
        default_factory=lambda: [
            ".git/**",
            ".env",
            ".envrc",
            "**/*.pem",
            "**/.ssh/**",
        ]
    )

    @field_validator("uploads_dir")
    @classmethod
    def _validate_uploads_dir(cls, value: str) -> str:
        if Path(value).is_absolute():
            raise ValueError("files.uploads_dir must be a relative path")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return 20 * 1024 * 1024

    @property
    def max_download_bytes(self) -> int:
        return 50 * 1024 * 1024


class TelegramTransportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bot_token: NonEmptyStr
    chat_id: StrictInt
    message_overflow: Literal["trim", "split"] = "trim"
    voice_transcription: bool = False
    voice_max_bytes: StrictInt = 10 * 1024 * 1024
    voice_transcription_model: NonEmptyStr = "gpt-4o-mini-transcribe"
    topics: TelegramTopicsSettings = Field(default_factory=TelegramTopicsSettings)
    files: TelegramFilesSettings = Field(default_factory=TelegramFilesSettings)


class TransportsSettings(BaseModel):
    telegram: TelegramTransportSettings

    model_config = ConfigDict(extra="allow")


class PluginsSettings(BaseModel):
    enabled: list[NonEmptyStr] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class ProjectSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: NonEmptyStr
    worktrees_dir: NonEmptyStr = ".worktrees"
    default_engine: NonEmptyStr | None = None
    worktree_base: NonEmptyStr | None = None
    chat_id: StrictInt | None = None


class TakopiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="allow",
        env_prefix="TAKOPI__",
        env_nested_delimiter="__",
        str_strip_whitespace=True,
    )

    watch_config: bool = False
    default_engine: NonEmptyStr = "codex"
    default_project: NonEmptyStr | None = None
    projects: dict[str, ProjectSettings] = Field(default_factory=dict)

    transport: NonEmptyStr = "telegram"
    transports: TransportsSettings

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
            alias = raw_alias
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

            path = _normalize_project_path(entry.path, config_path=config_path)

            worktrees_dir = Path(entry.worktrees_dir).expanduser()

            default_engine = None
            if entry.default_engine is not None:
                default_engine = _normalize_engine_id(
                    entry.default_engine,
                    engine_ids=engine_ids,
                    config_path=config_path,
                    label=f"projects.{alias}.default_engine",
                )

            worktree_base = entry.worktree_base

            chat_id = entry.chat_id
            if chat_id is not None:
                if chat_id == default_chat_id:
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
    return tg.bot_token, tg.chat_id


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
