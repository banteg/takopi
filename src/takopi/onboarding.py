from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from rich.console import Console
from rich.panel import Panel

from .config import ConfigError, HOME_CONFIG_PATH, load_telegram_config
from .engines import EngineBackend, SetupIssue

_OCTOPUS = "\N{OCTOPUS}"


@dataclass(slots=True)
class SetupResult:
    issues: list[SetupIssue]
    config_path: Path = HOME_CONFIG_PATH

    @property
    def ok(self) -> bool:
        return not self.issues


def config_issue(path: Path) -> SetupIssue:
    config_display = _config_path_display(path)
    return SetupIssue(
        "Create a config",
        (
            f"   [dim]{config_display}[/]",
            "",
            '   [cyan]bot_token[/] = [green]"123456789:ABCdef..."[/]',
            "   [cyan]chat_id[/]   = [green]123456789[/]",
            "",
            "[dim]" + ("-" * 56) + "[/]",
            "",
            "[bold]Getting your Telegram credentials:[/]",
            "",
            "   [cyan]bot_token[/]  create a bot with [link=https://t.me/BotFather]@BotFather[/]",
            "   [cyan]chat_id[/]    message [link=https://t.me/myidbot]@myidbot[/] to get your id",
        ),
    )


def check_setup(backend: EngineBackend) -> SetupResult:
    issues: list[SetupIssue] = []
    config_path = HOME_CONFIG_PATH
    config: dict = {}

    try:
        config, config_path = load_telegram_config()
    except ConfigError:
        issues.extend(backend.check_setup({}, config_path))
        issues.append(config_issue(config_path))
        return SetupResult(issues=issues, config_path=config_path)

    token = config.get("bot_token")
    chat_id = config.get("chat_id")

    missing_or_invalid_config = not (isinstance(token, str) and token.strip())
    missing_or_invalid_config |= type(chat_id) is not int

    issues.extend(backend.check_setup(config, config_path))
    if missing_or_invalid_config:
        issues.append(config_issue(config_path))

    return SetupResult(issues=issues, config_path=config_path)


def _config_path_display(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def render_setup_guide(result: SetupResult) -> None:
    if result.ok:
        return

    console = Console(stderr=True)
    parts: list[str] = []
    step = 0

    def add_step(title: str, *lines: str) -> None:
        nonlocal step
        step += 1
        parts.append(f"[bold yellow]{step}.[/] [bold]{title}[/]")
        parts.append("")
        parts.extend(lines)
        parts.append("")

    for issue in result.issues:
        add_step(issue.title, *issue.lines)

    panel = Panel(
        "\n".join(parts).rstrip(),
        title="[bold]welcome to takopi![/]",
        subtitle=f"{_OCTOPUS} setup required",
        border_style="yellow",
        padding=(1, 2),
        expand=False,
    )
    console.print(panel)


def render_engine_choice(backends: Sequence[EngineBackend]) -> None:
    console = Console(stderr=True)
    parts: list[str] = []
    parts.append("[bold]available engines:[/]")
    parts.append("")
    for idx, backend in enumerate(backends, start=1):
        parts.append(f"[bold yellow]{idx}.[/]")
        parts.append(f"   [dim]$[/] takopi {backend.id}")
        if backend.id == "claude":
            description = "use claude code"
        else:
            description = f"use {backend.display_name.lower()}"
        parts.append(f"   {description}")
        parts.append("")

    panel = Panel(
        "\n".join(parts).rstrip(),
        title="[bold]welcome to takopi![/]",
        subtitle=f"{_OCTOPUS} choose an engine",
        border_style="yellow",
        padding=(1, 2),
        expand=False,
    )
    console.print(panel)
