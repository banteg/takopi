from __future__ import annotations

import json
import logging
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import questionary
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import HOME_CONFIG_PATH
from .engines import list_backends
from .telegram import TelegramClient
from questionary.constants import DEFAULT_QUESTION_PREFIX
from questionary.question import Question
from questionary.styles import merge_styles_default


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


def _update_log_path() -> Path | None:
    value = os.environ.get("TAKOPI_ONBOARDING_LOG")
    if not value:
        return None
    if value.lower() in {"1", "true", "yes"}:
        return HOME_CONFIG_PATH.parent / "onboarding-updates.jsonl"
    return Path(value).expanduser()


def _append_update_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")


async def _get_bot_info(token: str) -> dict[str, Any] | None:
    bot = TelegramClient(token)
    try:
        return await bot.get_me()
    finally:
        await bot.close()


async def _wait_for_chat(token: str) -> ChatInfo:
    bot = TelegramClient(token)
    log_path = _update_log_path()
    try:
        offset: int | None = None
        allowed_updates = [
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "my_chat_member",
            "chat_member",
        ]
        drained = await bot.get_updates(
            offset=None, timeout_s=0, allowed_updates=allowed_updates
        )
        if log_path is not None:
            _append_update_log(
                log_path,
                {
                    "event": "drain",
                    "offset": offset,
                    "updates": drained,
                },
            )
        if drained:
            offset = drained[-1]["update_id"] + 1
        while True:
            updates = await bot.get_updates(
                offset=offset, timeout_s=50, allowed_updates=allowed_updates
            )
            if log_path is not None:
                _append_update_log(
                    log_path,
                    {
                        "event": "poll",
                        "offset": offset,
                        "updates": updates,
                    },
                )
            if updates is None:
                await anyio.sleep(1)
                continue
            if not updates:
                continue
            offset = updates[-1]["update_id"] + 1
            update = updates[-1]
            msg = update.get("message")
            if isinstance(msg, dict):
                sender = msg.get("from")
                if isinstance(sender, dict) and sender.get("is_bot") is True:
                    continue
                chat = msg.get("chat")
            else:
                chat = None
                for key in (
                    "edited_message",
                    "channel_post",
                    "edited_channel_post",
                    "my_chat_member",
                    "chat_member",
                ):
                    payload = update.get(key)
                    if isinstance(payload, dict):
                        chat = payload.get("chat")
                        break
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
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("agent")
    table.add_column("status")
    table.add_column("install command")
    for backend in backends:
        cmd = backend.cli_cmd or backend.id
        installed = shutil.which(cmd) is not None
        status = "[green]installed[/]" if installed else "[dim]not found[/]"
        rows.append((backend.id, installed, backend.install_cmd))
        table.add_row(
            backend.id,
            status,
            backend.install_cmd or "-",
        )
    console.print(table)
    return rows


@contextmanager
def _suppress_logging():
    prev_disable = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        yield
    finally:
        logging.disable(prev_disable)


def _confirm(message: str, *, default: bool = True) -> bool | None:
    merged_style = merge_styles_default([None])
    status = {"answer": None, "complete": False}

    def get_prompt_tokens():
        tokens = [
            ("class:qmark", DEFAULT_QUESTION_PREFIX),
            ("class:question", f" {message} "),
        ]
        if not status["complete"]:
            tokens.append(("class:instruction", "(yes/no) "))
        if status["answer"] is not None:
            tokens.append(("class:answer", "yes" if status["answer"] else "no"))
        return to_formatted_text(tokens)

    def exit_with_result(event):
        status["complete"] = True
        event.app.exit(result=status["answer"])

    bindings = KeyBindings()

    @bindings.add(Keys.ControlQ, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @bindings.add("n")
    @bindings.add("N")
    def key_n(event):
        status["answer"] = False
        exit_with_result(event)

    @bindings.add("y")
    @bindings.add("Y")
    def key_y(event):
        status["answer"] = True
        exit_with_result(event)

    @bindings.add(Keys.ControlH)
    def key_backspace(event):
        status["answer"] = None

    @bindings.add(Keys.ControlM, eager=True)
    def set_answer(event):
        if status["answer"] is None:
            status["answer"] = default
        exit_with_result(event)

    @bindings.add(Keys.Any)
    def other(event):
        _ = event

    question = Question(
        PromptSession(get_prompt_tokens, key_bindings=bindings, style=merged_style).app
    )
    return question.ask()


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
        retry = _confirm("try again?", default=True)
        if not retry:
            return None


def interactive_setup(*, force: bool) -> bool:
    console = Console()
    config_path = HOME_CONFIG_PATH

    suppress_logs = _suppress_logging()

    if config_path.exists() and not force:
        console.print(
            f"config already exists at {_display_path(config_path)}. "
            "use --onboard to reconfigure."
        )
        return True

    if config_path.exists() and force:
        overwrite = _confirm(
            f"overwrite existing config at {_display_path(config_path)}?",
            default=False,
        )
        if not overwrite:
            return False

    with suppress_logs:
        panel = Panel(
            "let's set up your telegram bot.",
            title="welcome to takopi!",
            border_style="yellow",
            padding=(1, 2),
            expand=False,
        )
        console.print(panel)

        console.print("step 1: telegram bot setup\n")
        have_token = _confirm("do you have a telegram bot token?")
        if have_token is None:
            return False
        if not have_token:
            console.print("  1. open telegram and message @BotFather")
            console.print("  2. send /newbot and follow the prompts")
            console.print("  3. copy the token (looks like 123456789:ABCdef...)")
            console.print("")

        token_info = _prompt_token(console)
        if token_info is None:
            return False
        token, _info = token_info

        console.print("")
        console.print(
            "  now send any message to your bot so we can capture your chat id"
        )
        console.print(
            "  for group chats, mention the bot or disable privacy via @BotFather"
        )
        log_path = _update_log_path()
        if log_path is not None:
            console.print(f"  logging updates to {_display_path(log_path)}")
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

        save = _confirm(
            f"save this config to {_display_path(config_path)}?",
            default=True,
        )
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
