from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import HOME_CONFIG_PATH
from .engines import list_backends
from .telegram import TelegramClient


@dataclass(frozen=True, slots=True)
class ChatInfo:
    chat_id: int
    username: str | None
    title: str | None
    first_name: str | None
    last_name: str | None
    chat_type: str | None

    @property
    def is_group(self) -> bool:
        return self.chat_type in {"group", "supergroup"}

    @property
    def display(self) -> str:
        if self.is_group:
            if self.title:
                return self.title
            return "group chat"
        if self.username:
            return f"@{self.username}"
        full_name = " ".join(part for part in [self.first_name, self.last_name] if part)
        return full_name or "private chat"


def _display_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _mask_token(token: str) -> str:
    token = token.strip()
    if len(token) <= 12:
        return "*" * len(token)
    return f"{token[:9]}...{token[-5:]}"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_config(token: str, chat_id: int, default_engine: str | None) -> str:
    lines: list[str] = []
    if default_engine:
        lines.append(f'default_engine = "{_toml_escape(default_engine)}"')
        lines.append("")
    lines.append(f'bot_token = "{_toml_escape(token)}"')
    lines.append(f"chat_id = {chat_id}")
    return "\n".join(lines) + "\n"


async def _get_bot_info(token: str) -> dict[str, Any] | None:
    bot = TelegramClient(token)
    try:
        return await bot.get_me()
    finally:
        await bot.close()


async def _wait_for_chat(token: str) -> ChatInfo:
    bot = TelegramClient(token)
    try:
        offset: int | None = None
        drained = await bot.get_updates(
            offset=None, timeout_s=0, allowed_updates=["message"]
        )
        if drained:
            offset = drained[-1]["update_id"] + 1
        while True:
            updates = await bot.get_updates(
                offset=offset, timeout_s=50, allowed_updates=["message"]
            )
            if updates is None:
                await anyio.sleep(1)
                continue
            if not updates:
                continue
            offset = updates[-1]["update_id"] + 1
            update = updates[-1]
            msg = update.get("message")
            if not isinstance(msg, dict):
                continue
            sender = msg.get("from")
            if isinstance(sender, dict) and sender.get("is_bot") is True:
                continue
            chat = msg.get("chat")
            if not isinstance(chat, dict):
                continue
            chat_id = chat.get("id")
            if not isinstance(chat_id, int):
                continue
            return ChatInfo(
                chat_id=chat_id,
                username=chat.get("username")
                if isinstance(chat.get("username"), str)
                else None,
                title=chat.get("title") if isinstance(chat.get("title"), str) else None,
                first_name=chat.get("first_name")
                if isinstance(chat.get("first_name"), str)
                else None,
                last_name=chat.get("last_name")
                if isinstance(chat.get("last_name"), str)
                else None,
                chat_type=chat.get("type")
                if isinstance(chat.get("type"), str)
                else None,
            )
    finally:
        await bot.close()


async def _send_confirmation(token: str, chat_id: int) -> bool:
    bot = TelegramClient(token)
    try:
        res = await bot.send_message(
            chat_id=chat_id,
            text="takopi is configured and ready.",
        )
        return res is not None
    finally:
        await bot.close()


def _render_engine_table(console: Console) -> list[tuple[str, bool, str | None]]:
    backends = list_backends()
    rows: list[tuple[str, bool, str | None]] = []
    table = Table(show_header=True, header_style="bold")
    table.add_column("agent")
    table.add_column("status")
    table.add_column("install command")
    for backend in backends:
        cmd = backend.cli_cmd or backend.id
        installed = shutil.which(cmd) is not None
        status = "installed" if installed else "not found"
        rows.append((backend.id, installed, backend.install_cmd))
        table.add_row(
            backend.id,
            status,
            backend.install_cmd or "-",
        )
    console.print(table)
    return rows


def _prompt_token(console: Console) -> tuple[str, dict[str, Any]] | None:
    while True:
        token = questionary.password("paste your bot token:").ask()
        if token is None:
            return None
        token = token.strip()
        if not token:
            console.print("  token cannot be empty")
            continue
        console.print("  validating...")
        info = anyio.run(_get_bot_info, token)
        if info:
            username = info.get("username")
            if isinstance(username, str) and username:
                console.print(f"  connected to @{username}")
            else:
                name = info.get("first_name") or "your bot"
                console.print(f"  connected to {name}")
            return token, info
        console.print("  failed to connect, check the token and try again")
        retry = questionary.confirm("try again?", default=True).ask()
        if not retry:
            return None


def interactive_setup(*, force: bool) -> bool:
    console = Console()
    config_path = HOME_CONFIG_PATH

    if config_path.exists() and not force:
        console.print(
            f"config already exists at {_display_path(config_path)}. "
            "use --onboard to reconfigure."
        )
        return True

    if config_path.exists() and force:
        overwrite = questionary.confirm(
            f"overwrite existing config at {_display_path(config_path)}?",
            default=False,
        ).ask()
        if not overwrite:
            return False

    panel = Panel(
        "let's set up your telegram bot.",
        title="welcome to takopi!",
        border_style="yellow",
        padding=(1, 2),
        expand=False,
    )
    console.print(panel)

    console.print("step 1: telegram bot setup\n")
    have_token = questionary.confirm("do you have a telegram bot token?").ask()
    if have_token is None:
        return False
    if not have_token:
        console.print("  1. open telegram and message @BotFather")
        console.print("  2. send /newbot and follow the prompts")
        console.print("  3. copy the token (looks like 123456789:ABCdef...)")
        console.print("")
        questionary.text("press enter when you have your token...").ask()

    token_info = _prompt_token(console)
    if token_info is None:
        return False
    token, _info = token_info

    console.print("")
    console.print("  now send any message to your bot so we can capture your chat id")
    console.print("  waiting for message... (press ctrl+c to cancel)")
    try:
        chat = anyio.run(_wait_for_chat, token)
    except KeyboardInterrupt:
        console.print("  cancelled")
        return False
    if chat is None:
        console.print("  cancelled")
        return False
    console.print(f"  got chat_id {chat.chat_id} from {chat.display}")

    sent = anyio.run(_send_confirmation, token, chat.chat_id)
    if sent:
        console.print("  sent confirmation message")
    else:
        console.print("  could not send confirmation message")

    console.print("\nstep 2: agent cli tools\n")
    rows = _render_engine_table(console)
    installed_ids = [engine_id for engine_id, installed, _ in rows if installed]

    default_engine: str | None = None
    if installed_ids:
        default_engine = questionary.select(
            "choose default agent:",
            choices=installed_ids,
        ).ask()
        if default_engine is None:
            return False
    else:
        console.print("no agents found on PATH. install one to continue.")

    config_preview = _render_config(
        _mask_token(token),
        chat.chat_id,
        default_engine,
    ).rstrip()
    console.print("\nstep 3: save configuration\n")
    console.print(f"  {_display_path(config_path)}\n")
    for line in config_preview.splitlines():
        console.print(f"  {line}")
    console.print("")

    save = questionary.confirm(
        f"save this config to {_display_path(config_path)}?", default=True
    ).ask()
    if not save:
        return False

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_text = _render_config(token, chat.chat_id, default_engine)
    config_path.write_text(config_text, encoding="utf-8")
    console.print(f"  config saved to {_display_path(config_path)}")

    done_panel = Panel(
        "setup complete. starting takopi...",
        border_style="green",
        padding=(1, 2),
        expand=False,
    )
    console.print("\n")
    console.print(done_panel)
    return True
