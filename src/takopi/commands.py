"""Command catalog for slash command prompts.

Commands are loaded from markdown files in configured directories.
Each file becomes a slash command with the filename (sans extension) as the command name.
The file's first line (if it starts with #) becomes the description; the rest is the prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .logging import get_logger

logger = get_logger(__name__)

_COMMAND_NORMALIZE_RE = re.compile(r"[^a-z0-9_]")


@dataclass(frozen=True, slots=True)
class Command:
    """A single slash command with its prompt template."""

    name: str
    description: str
    prompt: str
    location: Path
    source: str


@dataclass(frozen=True, slots=True)
class CommandCatalog:
    """Collection of commands indexed by name and normalized command."""

    commands: tuple[Command, ...] = ()
    by_name: dict[str, Command] = field(default_factory=dict)
    by_command: dict[str, Command] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> CommandCatalog:
        return cls()

    @classmethod
    def from_commands(cls, commands: Iterable[Command]) -> CommandCatalog:
        by_name: dict[str, Command] = {}
        by_command: dict[str, Command] = {}
        order: list[str] = []
        for command in commands:
            name_key = command.name.strip().lower()
            if not name_key:
                continue
            if name_key in by_name:
                logger.warning(
                    "commands.duplicate",
                    name=command.name,
                    existing=str(by_name[name_key].location),
                    duplicate=str(command.location),
                )
            else:
                order.append(name_key)
            by_name[name_key] = command
            command_key = normalize_command(command.name)
            if command_key:
                if (
                    command_key in by_command
                    and by_command[command_key].name != command.name
                ):
                    logger.warning(
                        "commands.command_conflict",
                        command=command_key,
                        existing=by_command[command_key].name,
                        duplicate=command.name,
                    )
                by_command[command_key] = command
        deduped = tuple(by_name[key] for key in order)
        return cls(commands=deduped, by_name=by_name, by_command=by_command)


def normalize_command(name: str) -> str:
    """Normalize a command name to lowercase alphanumeric with underscores."""
    value = name.strip().lstrip("/").lower()
    if not value:
        return ""
    value = _COMMAND_NORMALIZE_RE.sub("_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def build_command_prompt(command: Command, args_text: str) -> str:
    """Combine a command's prompt template with user-provided arguments."""
    prompt = command.prompt.strip()
    args = args_text.strip()
    if prompt and args:
        return f"{prompt}\n\n{args}"
    return prompt or args or ""


def parse_command_dirs(config: dict) -> list[Path]:
    """Parse command_dirs from config, returning list of Path objects."""
    value = config.get("command_dirs")
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [item for item in value if isinstance(item, str)]
    else:
        logger.warning("commands.invalid_dirs", value=repr(value))
        return []
    roots: list[Path] = []
    for item in items:
        path = Path(item).expanduser()
        if path.is_dir():
            roots.append(path)
        else:
            logger.warning("commands.dir_not_found", path=str(path))
    return roots


def _parse_command_file(path: Path, source: str) -> Command | None:
    """Parse a single command file, returning Command or None on error."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("commands.read_error", path=str(path), error=str(exc))
        return None

    name = path.stem
    lines = content.splitlines()

    # Extract description from first line if it's a heading
    description = f"run {name}"
    prompt_start = 0
    if lines and lines[0].startswith("#"):
        heading = lines[0].lstrip("#").strip()
        if heading:
            description = heading
        prompt_start = 1

    prompt = "\n".join(lines[prompt_start:]).strip()
    if not prompt:
        logger.warning("commands.empty_prompt", path=str(path))
        return None

    return Command(
        name=name,
        description=description,
        prompt=prompt,
        location=path,
        source=source,
    )


def load_commands_from_dirs(dirs: list[Path]) -> CommandCatalog:
    """Load all commands from the given directories."""
    commands: list[Command] = []
    for directory in dirs:
        source = str(directory)
        for path in sorted(directory.glob("*.md")):
            if path.is_file():
                command = _parse_command_file(path, source)
                if command is not None:
                    commands.append(command)
    return CommandCatalog.from_commands(commands)
