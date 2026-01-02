from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


from rich.console import Console
from rich.panel import Panel

from .backends import EngineBackend, SetupIssue
from .backends_helpers import install_issue
from .config import ConfigError, HOME_CONFIG_PATH, load_telegram_config

_OCTOPUS = "\N{OCTOPUS}"


@dataclass(slots=True)
class SetupResult:
    issues: list[SetupIssue]
    config_path: Path = HOME_CONFIG_PATH

    @property
    def ok(self) -> bool:
        return not self.issues


def config_issue(path: Path) -> SetupIssue:
    config_display = _config_path_display(_preferred_config_path(path))
    return SetupIssue(
        "create a config",
        (
            f"   [dim]{config_display}[/]",
            "",
            '   [cyan]bot_token[/] = [green]"123456789:ABCdef..."[/]',
            "   [cyan]chat_id[/]   = [green]123456789[/]",
            "",
            "[dim]" + ("-" * 56) + "[/]",
            "",
            "[bold]getting your telegram credentials:[/]",
            "",
            "   [cyan]bot_token[/]  create a bot with [link=https://t.me/BotFather]@BotFather[/]",
            "   [cyan]chat_id[/]    message [link=https://t.me/myidbot]@myidbot[/] to get your id",
        ),
    )


def check_setup(backend: EngineBackend) -> SetupResult:
    issues: list[SetupIssue] = []
    config_path = HOME_CONFIG_PATH
    config: dict = {}
    cmd = backend.cli_cmd or backend.id
    backend_issues: list[SetupIssue] = []
    if shutil.which(cmd) is None:
        backend_issues.append(install_issue(cmd, backend.install_cmd))

    try:
        config, config_path = load_telegram_config()
    except ConfigError:
        issues.extend(backend_issues)
        issues.append(config_issue(config_path))
        return SetupResult(issues=issues, config_path=config_path)

    token = config.get("bot_token")
    chat_id = config.get("chat_id")

    missing_or_invalid_config = not (isinstance(token, str) and token.strip())
    missing_or_invalid_config |= type(chat_id) is not int

    issues.extend(backend_issues)
    if missing_or_invalid_config:
        issues.append(config_issue(config_path))

    return SetupResult(issues=issues, config_path=config_path)


def _config_path_display(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _preferred_config_path(path: Path) -> Path:
    if path.name == "takopi.toml" and path.parent.name == ".codex":
        return path.parent.parent / ".takopi" / "takopi.toml"
    return path


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
