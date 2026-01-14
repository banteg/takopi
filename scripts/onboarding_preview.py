from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

import anyio
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from takopi.config import ConfigError
from takopi.telegram import onboarding as ob
from takopi.telegram.api_models import User


def section(console: Console, title: str) -> None:
    console.print("")
    console.print(f"=== {title} ===", markup=False)


def render_confirm(console: Console, prompt: str) -> None:
    console.print(f"? {prompt} (yes/no)", markup=False)


def render_password(console: Console, prompt: str) -> None:
    console.print(f"? {prompt} {'*' * 28}", markup=False)


def render_select(console: Console, prompt: str, choices: list[str]) -> None:
    console.print(f"? {prompt} (use arrow keys)", markup=False)
    for index, choice in enumerate(choices):
        marker = ">" if index == 0 else " "
        console.print(f"{marker} {choice}", markup=False)


def next_value(values: Iterator[Any], label: str) -> Any:
    try:
        return next(values)
    except StopIteration as exc:
        raise RuntimeError(f"scripted ui ran out of {label} responses") from exc


class ScriptedUI:
    def __init__(
        self,
        console: Console,
        *,
        confirms: Iterable[bool | None],
        selects: Iterable[Any],
        passwords: Iterable[str | None],
    ) -> None:
        self._console = console
        self._confirms = iter(confirms)
        self._selects = iter(selects)
        self._passwords = iter(passwords)

    @property
    def console(self) -> Console:
        return self._console

    def panel(
        self,
        title: str | None,
        body: str,
        *,
        border_style: str = "yellow",
    ) -> None:
        panel = Panel(
            body,
            title=title,
            border_style=border_style,
            padding=(1, 2),
            expand=False,
        )
        self._console.print(panel)

    def step(self, title: str, *, number: int) -> None:
        self._console.print("")
        self._console.print(Text(f"step {number}: {title}", style="bold yellow"))
        self._console.print("")

    def print(self, text: object = "", *, markup: bool | None = None) -> None:
        if markup is None:
            self._console.print(text)
            return
        self._console.print(text, markup=markup)

    async def confirm(self, prompt: str, default: bool = True) -> bool | None:
        render_confirm(self._console, prompt)
        return next_value(self._confirms, "confirm")

    async def select(self, prompt: str, choices: list[tuple[str, Any]]) -> Any | None:
        rendered = [label for label, _value in choices]
        render_select(self._console, prompt, rendered)
        return next_value(self._selects, "select")

    async def password(self, prompt: str) -> str | None:
        render_password(self._console, prompt)
        return next_value(self._passwords, "password")


@dataclass
class ScriptedServices:
    bot: User
    chat: ob.ChatInfo
    engines: list[tuple[str, bool, str | None]]
    topics_issue: ConfigError | None = None
    existing_config: dict[str, Any] | None = None
    written_config: dict[str, Any] | None = None

    async def get_bot_info(self, _token: str) -> User | None:
        return self.bot

    async def wait_for_chat(self, _token: str) -> ob.ChatInfo:
        return self.chat

    async def validate_topics(
        self, _token: str, _chat_id: int, _scope: ob.TopicScope
    ) -> ConfigError | None:
        return self.topics_issue

    def list_engines(self) -> list[tuple[str, bool, str | None]]:
        return self.engines

    def read_config(self, _path) -> dict[str, Any]:
        return dict(self.existing_config or {})

    def write_config(self, _path, data: dict[str, Any]) -> None:
        self.written_config = data


async def run_flow(title: str, ui: ScriptedUI, svc: ScriptedServices) -> None:
    section(ui.console, title)
    state = ob.OnboardingState(config_path=ob.HOME_CONFIG_PATH, force=False)
    await ob.run_onboarding(ui, svc, state)


def main() -> None:
    console = Console()

    bot = User(id=1, username="bunny_agent_bot", first_name="Bunny")
    group_chat = ob.ChatInfo(
        chat_id=-1001234567890,
        username=None,
        title="takopi devs",
        first_name=None,
        last_name=None,
        chat_type="supergroup",
    )
    private_chat = ob.ChatInfo(
        chat_id=462722,
        username="banteg",
        title=None,
        first_name="Banteg",
        last_name=None,
        chat_type="private",
    )
    engines_installed = [
        ("codex", True, "brew install codex"),
        ("claude", True, "brew install claude"),
        ("opencode", False, "brew install opencode"),
    ]
    engines_missing = [
        ("codex", False, "brew install codex"),
        ("claude", False, "brew install claude"),
        ("opencode", False, "brew install opencode"),
    ]

    anyio.run(
        run_flow,
        "assistant mode (private chat)",
        ScriptedUI(
            console,
            confirms=[True, True],
            selects=["assistant", "codex"],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(bot=bot, chat=private_chat, engines=engines_installed),
    )

    anyio.run(
        run_flow,
        "handoff mode (token instructions)",
        ScriptedUI(
            console,
            confirms=[False, True],
            selects=["handoff", "codex"],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(bot=bot, chat=private_chat, engines=engines_installed),
    )

    anyio.run(
        run_flow,
        "workspace mode (topics)",
        ScriptedUI(
            console,
            confirms=[True, True],
            selects=["workspace", "codex"],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(bot=bot, chat=group_chat, engines=engines_installed),
    )

    anyio.run(
        run_flow,
        "topics validation warning",
        ScriptedUI(
            console,
            confirms=[True, True],
            selects=["workspace", "assistant", "codex"],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(
            bot=bot,
            chat=group_chat,
            engines=engines_installed,
            topics_issue=ConfigError("bot is missing admin rights"),
        ),
    )

    anyio.run(
        run_flow,
        "no engines installed",
        ScriptedUI(
            console,
            confirms=[True, False],
            selects=["assistant"],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(bot=bot, chat=private_chat, engines=engines_missing),
    )

if __name__ == "__main__":
    main()
