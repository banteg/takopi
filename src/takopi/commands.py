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
    name: str
    description: str
    prompt: str
    location: Path
    source: str


@dataclass(frozen=True, slots=True)
class CommandCatalog:
    commands: tuple[Command, ...] = ()
    by_name: dict[str, Command] = field(default_factory=dict)
    by_command: dict[str, Command] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "CommandCatalog":
        return cls()

    @classmethod
    def from_commands(cls, commands: Iterable[Command]) -> "CommandCatalog":
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
    value = name.strip().lstrip("/").lower()
    if not value:
        return ""
    value = _COMMAND_NORMALIZE_RE.sub("_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def build_command_prompt(command: Command, args_text: str) -> str:
    prompt = command.prompt.strip()
    args = args_text.strip()
    if prompt and args:
        return f"{prompt}\n\n{args}"
    return prompt or args or ""


def parse_command_dirs(config: dict) -> list[Path]:
    value = config.get("command_dirs")
    if value is None:
        value = config.get("skill_dirs")
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
        roots.append(path)
    return roots


def load_commands(
    *,
    cwd: Path,
    extra_roots: Iterable[Path] = (),
    include_parents: bool = True,
    include_home: bool = True,
) -> CommandCatalog:
    roots: list[Path] = []
    cwd = cwd.resolve()
    roots.append(cwd)
    if include_parents:
        roots.extend(cwd.parents)
    for extra in extra_roots:
        roots.append(extra.expanduser())
    if include_home:
        home = Path.home().resolve()
        if home not in roots:
            roots.append(home)
    seen: set[Path] = set()
    commands: list[Command] = []
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        commands.extend(_scan_root(root))
    return CommandCatalog.from_commands(commands)


def _scan_root(root: Path) -> list[Command]:
    commands: list[Command] = []
    patterns = (
        (root / ".opencode" / "command", "**/*.md", "opencode-command"),
        (root / ".claude" / "commands", "**/*.md", "claude-command"),
    )
    for base, glob, source in patterns:
        if not base.is_dir():
            continue
        for path in base.rglob(glob):
            if not path.is_file():
                continue
            command = _parse_command_file(path, base=base, source=source)
            if command is not None:
                commands.append(command)
    return commands


def _parse_command_file(
    path: Path,
    *,
    base: Path,
    source: str,
) -> Command | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("commands.read_failed", path=str(path), error=str(exc))
        return None
    frontmatter = _parse_frontmatter(text)
    prompt: str
    meta: dict[str, str]
    if frontmatter is not None:
        meta, prompt = frontmatter
    else:
        meta, prompt = {}, text.strip()
    if not prompt:
        return None
    name = meta.get("name") or _command_name(path, base=base)
    if not name:
        return None
    description = meta.get("description") or _first_non_empty_line(prompt) or name
    return Command(
        name=name,
        description=description,
        prompt=prompt,
        location=path,
        source=source,
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end_idx = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end_idx is None:
        return None
    meta = _parse_frontmatter_lines(lines[1:end_idx])
    content = "\n".join(lines[end_idx + 1 :]).strip()
    return meta, content


def _command_name(path: Path, *, base: Path) -> str:
    try:
        rel = path.relative_to(base).with_suffix("")
    except ValueError:
        rel = path.with_suffix("").name
    return str(rel).replace("\\", "/")


def _parse_frontmatter_lines(lines: list[str]) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        meta[key] = value
    return meta


def _first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
