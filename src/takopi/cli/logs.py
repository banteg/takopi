from __future__ import annotations

from pathlib import Path

import typer

from ..config import ConfigError
from ..event_log_sqlite import build_sqlite_from_jsonl
from ..settings import load_settings


def logs_rebuild(
    jsonl: Path | None = typer.Option(
        None, "--jsonl", help="Override JSONL log path."
    ),
    sqlite: Path | None = typer.Option(
        None, "--sqlite", help="Override SQLite cache path."
    ),
) -> None:
    """Rebuild the SQLite cache from JSONL log events."""
    settings, _ = load_settings()
    logging = settings.logging
    if not logging.enabled:
        raise ConfigError("Transcript logging is disabled (set logging.enabled = true).")
    jsonl_path = Path(jsonl or logging.events_jsonl).expanduser()
    sqlite_path_value = sqlite or logging.events_sqlite
    if sqlite_path_value is None:
        raise ConfigError("logging.events_sqlite is not set.")
    sqlite_path = Path(sqlite_path_value).expanduser()
    rows = build_sqlite_from_jsonl(jsonl_path, sqlite_path)
    typer.echo(f"rebuilt {rows} rows -> {sqlite_path}")
