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
    console.print(f"? {prompt} (Use arrow keys)", markup=False)
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
        self._console.print(Text(f"step {number}: {title}", style="bold yellow"))

    def print(self, text: object = "", *, markup: bool | None = None) -> None:
        if markup is None:
            self._console.print(text)
            return
        self._console.print(text, markup=markup)

    def confirm(self, prompt: str, default: bool = True) -> bool | None:
        render_confirm(self._console, prompt)
        return next_value(self._confirms, "confirm")

    def select(self, prompt: str, choices: list[tuple[str, Any]]) -> Any | None:
        rendered = [label for label, _value in choices]
        render_select(self._console, prompt, rendered)
        return next_value(self._selects, "select")

    def password(self, prompt: str) -> str | None:
        render_password(self._console, prompt)
        return next_value(self._passwords, "password")


@dataclass
class ScriptedServices:
    bot: User
    chat: ob.ChatInfo
    engines: list[tuple[str, bool, str | None]]
    topics_issue: ConfigError | None = None
    send_confirmation_result: bool = True
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

    async def send_confirmation(
        self, _token: str, _chat_id: int, _text: str
    ) -> bool:
        return self.send_confirmation_result

    def list_engines(self) -> list[tuple[str, bool, str | None]]:
        return self.engines

    def read_config(self, _path) -> dict[str, Any]:
        return dict(self.existing_config or {})

    def write_config(self, _path, data: dict[str, Any]) -> None:
        self.written_config = data


async def run_flow(title: str, ui: ScriptedUI, svc: ScriptedServices) -> None:
    section(ui.console, title)
    ui.panel(
        "welcome to takopi!",
        f"let's set up your telegram bot.\n"
        f"we'll write {ob.display_path(ob.HOME_CONFIG_PATH)}.",
        border_style="yellow",
    )
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
        "happy path (group chat, topics off)",
        ScriptedUI(
            console,
            confirms=[True, True],
            selects=["chat", "disabled", False, "codex"],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(bot=bot, chat=group_chat, engines=engines_installed),
    )

    anyio.run(
        run_flow,
        "private chat (topics projects, token instructions)",
        ScriptedUI(
            console,
            confirms=[False, True],
            selects=["stateless", "projects", True, "codex"],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(bot=bot, chat=private_chat, engines=engines_installed),
    )

    anyio.run(
        run_flow,
        "topics validation warning",
        ScriptedUI(
            console,
            confirms=[True, True, True],
            selects=["chat", "main", False, "codex"],
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
            selects=["chat", "disabled", False],
            passwords=["123456789:ABCdef"],
        ),
        ScriptedServices(bot=bot, chat=group_chat, engines=engines_missing),
    )

    section(console, "telegram confirmation messages")
    variants = [
        ("chat", False, True),
        ("chat", True, False),
        ("stateless", False, True),
        ("stateless", True, True),
    ]
    for session_mode, topics_enabled, show_resume_line in variants:
        title = f"mode={session_mode}, topics={topics_enabled}, resume={show_resume_line}"
        console.print("")
        console.print(Text(title, style="bold"))
        message = ob.build_confirmation_message(
            session_mode=session_mode,
            topics_enabled=topics_enabled,
            show_resume_line=show_resume_line,
        )
        for line in message.splitlines():
            console.print(f"  {line}", markup=False)


if __name__ == "__main__":
    main()
