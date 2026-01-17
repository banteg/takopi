from __future__ import annotations

import shutil
from contextlib import contextmanager
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import anyio
import questionary
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from questionary.constants import DEFAULT_QUESTION_PREFIX
from questionary.question import Question
from questionary.styles import merge_styles_default
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..backends import EngineBackend, SetupIssue
from ..backends_helpers import install_issue
from ..config import (
    ConfigError,
    ensure_table,
    read_config,
    write_config,
)
from ..engines import list_backends
from ..logging import suppress_logs
from ..settings import (
    HOME_CONFIG_PATH,
    TelegramTopicsSettings,
    load_settings,
    require_telegram,
)
from ..transports import SetupResult
from .api_models import User
from .client import TelegramClient, TelegramRetryAfter
from .topics import _validate_topics_setup_for

__all__ = [
    "ChatInfo",
    "check_setup",
    "debug_onboarding_paths",
    "interactive_setup",
    "mask_token",
    "get_bot_info",
    "wait_for_chat",
]

TopicScope = Literal["auto", "main", "projects", "all"]
SessionMode = Literal["chat", "stateless"]
Persona = Literal["workspace", "assistant", "handoff"]


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
                return f'group "{self.title}"'
            return "group chat"
        if self.chat_type == "channel":
            if self.title:
                return f'channel "{self.title}"'
            return "channel"
        if self.username:
            return f"@{self.username}"
        full_name = " ".join(part for part in [self.first_name, self.last_name] if part)
        return full_name or "private chat"

    @property
    def kind(self) -> str:
        if self.chat_type in {None, "private"}:
            return "private chat"
        if self.chat_type in {"group", "supergroup"}:
            if self.title:
                return f'{self.chat_type} "{self.title}"'
            return self.chat_type
        if self.chat_type == "channel":
            if self.title:
                return f'channel "{self.title}"'
            return "channel"
        if self.chat_type:
            return self.chat_type
        return "unknown chat"


@dataclass(slots=True)
class OnboardingState:
    config_path: Path
    force: bool

    token: str | None = None
    bot_username: str | None = None
    bot_name: str | None = None
    chat: ChatInfo | None = None
    persona: Persona | None = None

    session_mode: SessionMode | None = None
    topics_enabled: bool = False
    topics_scope: TopicScope = "auto"
    show_resume_line: bool | None = None
    default_engine: str | None = None

    @property
    def is_stateful(self) -> bool:
        return self.session_mode == "chat" or self.topics_enabled

    @property
    def bot_ref(self) -> str:
        if self.bot_username:
            return f"@{self.bot_username}"
        if self.bot_name:
            return self.bot_name
        return "your bot"


class OnboardingCancelled(Exception):
    pass


def require_value(value: Any) -> Any:
    if value is None:
        raise OnboardingCancelled()
    return value


class UI(Protocol):
    def panel(
        self,
        title: str | None,
        body: str,
        *,
        border_style: str = "yellow",
    ) -> None: ...

    def step(self, title: str, *, number: int) -> None: ...
    def print(self, text: object = "", *, markup: bool | None = None) -> None: ...
    async def confirm(self, prompt: str, default: bool = True) -> bool | None: ...
    async def select(
        self, prompt: str, choices: list[tuple[str, Any]]
    ) -> Any | None: ...
    async def password(self, prompt: str) -> str | None: ...


class Services(Protocol):
    async def get_bot_info(self, token: str) -> User | None: ...
    async def wait_for_chat(self, token: str) -> ChatInfo: ...

    async def validate_topics(
        self, token: str, chat_id: int, scope: TopicScope
    ) -> ConfigError | None: ...

    def list_engines(self) -> list[tuple[str, bool, str | None]]: ...
    def read_config(self, path: Path) -> dict[str, Any]: ...
    def write_config(self, path: Path, data: dict[str, Any]) -> None: ...


def display_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


_CREATE_CONFIG_TITLE = "create a config"
_CONFIGURE_TELEGRAM_TITLE = "configure telegram"


def config_issue(path: Path, *, title: str) -> SetupIssue:
    return SetupIssue(title, (f"   {display_path(path)}",))


def check_setup(
    backend: EngineBackend,
    *,
    transport_override: str | None = None,
) -> SetupResult:
    issues: list[SetupIssue] = []
    config_path = HOME_CONFIG_PATH
    cmd = backend.cli_cmd or backend.id
    backend_issues: list[SetupIssue] = []
    if shutil.which(cmd) is None:
        backend_issues.append(install_issue(cmd, backend.install_cmd))

    try:
        settings, config_path = load_settings()
        if transport_override:
            settings = settings.model_copy(update={"transport": transport_override})
        try:
            require_telegram(settings, config_path)
        except ConfigError:
            issues.append(config_issue(config_path, title=_CONFIGURE_TELEGRAM_TITLE))
    except ConfigError:
        issues.extend(backend_issues)
        title = (
            _CONFIGURE_TELEGRAM_TITLE
            if config_path.exists() and config_path.is_file()
            else _CREATE_CONFIG_TITLE
        )
        issues.append(config_issue(config_path, title=title))
        return SetupResult(issues=issues, config_path=config_path)

    issues.extend(backend_issues)
    return SetupResult(issues=issues, config_path=config_path)


def mask_token(token: str) -> str:
    token = token.strip()
    if len(token) <= 12:
        return "*" * len(token)
    return f"{token[:9]}...{token[-5:]}"


async def get_bot_info(
    token: str,
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> User | None:
    if sleep is None:
        sleep = anyio.sleep
    bot = TelegramClient(token)
    try:
        for _ in range(3):
            try:
                return await bot.get_me()
            except TelegramRetryAfter as exc:
                await sleep(exc.retry_after)
        return None
    finally:
        await bot.close()


async def wait_for_chat(
    token: str,
    *,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> ChatInfo:
    if sleep is None:
        sleep = anyio.sleep
    bot = TelegramClient(token)
    try:
        offset: int | None = None
        allowed_updates = ["message"]
        drained = await bot.get_updates(
            offset=None, timeout_s=0, allowed_updates=allowed_updates
        )
        if drained:
            offset = drained[-1].update_id + 1
        while True:
            updates = await bot.get_updates(
                offset=offset, timeout_s=50, allowed_updates=allowed_updates
            )
            if updates is None:
                await sleep(1)
                continue
            if not updates:
                continue
            update = updates[-1]
            offset = update.update_id + 1
            msg = update.message
            if msg is None:
                continue
            sender = msg.from_
            if sender is not None and sender.is_bot is True:
                continue
            chat = msg.chat
            if chat is None:
                continue
            chat_id = chat.id
            return ChatInfo(
                chat_id=chat_id,
                username=chat.username,
                title=chat.title,
                first_name=chat.first_name,
                last_name=chat.last_name,
                chat_type=chat.type,
            )
    finally:
        await bot.close()


def render_engine_table(ui: UI, rows: list[tuple[str, bool, str | None]]) -> None:
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("engine")
    table.add_column("status")
    table.add_column("install command")
    for engine_id, installed, install_cmd in rows:
        status = "[green]✓ installed[/]" if installed else "[dim]✗ not found[/]"
        table.add_row(
            engine_id,
            status,
            "" if installed else (install_cmd or "-"),
        )
    ui.print(table)


def append_dialogue(
    text: Text,
    speaker: str,
    message: str,
    *,
    speaker_style: str,
    message_style: str | None = None,
) -> None:
    text.append(f"[{speaker}] ", style=speaker_style)
    text.append(message, style=message_style)
    text.append("\n")


def render_private_chat_instructions(bot_ref: str) -> Text:
    return Text.assemble(
        f"  1. open a chat with {bot_ref}\n",
        "  2. send /start\n",
    )


def render_topics_group_instructions(bot_ref: str) -> Text:
    return Text.assemble(
        "  set up a topics group:\n",
        "  1. create a group and enable topics (settings → topics)\n",
        f'  2. add {bot_ref} as admin with "manage topics"\n',
        "  3. send any message in the group\n",
    )


def render_generic_capture_prompt(bot_ref: str) -> Text:
    return Text.assemble(
        f"  send /start to {bot_ref} in the chat you want takopi to use "
        "(private chat or group)"
    )


def render_botfather_instructions() -> Text:
    return Text.assemble(
        "  1. open telegram and message @BotFather\n",
        "  2. send /newbot and follow the prompts\n",
        "  3. copy the token (looks like 123456789:ABCdef...)",
    )


def render_topics_validation_warning(issue: ConfigError) -> Text:
    return Text.assemble(
        ("warning: ", "yellow"),
        f"topics validation failed: {issue}\n",
        '  ensure the bot is admin with "manage topics" permission.',
    )


def render_config_malformed_warning(error: ConfigError) -> Text:
    return Text.assemble(("warning: ", "yellow"), f"config is malformed: {error}")


def render_backup_failed_warning(error: OSError) -> Text:
    return Text.assemble(("warning: ", "yellow"), f"failed to back up config: {error}")


def render_persona_tabs() -> Table:
    active_label = "happian @memory-box"
    inactive_label = "takopi @master"
    grid = Table.grid(padding=(0, 2))
    grid.pad_edge = False
    grid.add_column()
    grid.add_column()
    grid.add_row(Text(active_label, style="cyan"), Text(inactive_label, style="dim"))
    grid.add_row(Text("─" * len(active_label), style="cyan"), Text(""))
    return grid


def render_workspace_preview() -> Text:
    return Text.assemble(
        ("[bot] ", "bold magenta"),
        ("topic bound to @memory-box\n", "dim"),
        ("[you] ", "bold cyan"),
        "store artifacts forever\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 10s\n", "dim"),
        ("[you] ", "bold cyan"),
        "also freeze them\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 6s\n", "dim"),
        ("[you] ", "bold cyan"),
        "automatically adjust size\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 6s", "dim"),
    )


def render_assistant_preview() -> Text:
    return Text.assemble(
        ("[you] ", "bold cyan"),
        "make happy wings fit\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 8s\n", "dim"),
        ("[you] ", "bold cyan"),
        "carry heavy creatures\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 12s\n", "dim"),
        ("[you] ", "bold cyan"),
        ("/new", "green"),
        ("  ← start fresh\n", "yellow"),
        ("[you] ", "bold cyan"),
        "add flower pin\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 6s\n", "dim"),
        ("[you] ", "bold cyan"),
        "make wearer appear as flower\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 4s", "dim"),
    )


def render_handoff_preview() -> Text:
    return Text.assemble(
        ("[you] ", "bold cyan"),
        "make it go back in time\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 8s\n", "dim"),
        ("      codex resume ", "dim"),
        ("abc123 ", "cyan"),
        ("← reply\n", "yellow"),
        ("[you] ", "bold cyan"),
        "add reconciliation ribbon\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 3s\n", "dim"),
        ("      codex resume ", "dim"),
        ("def456\n", "blue"),
        ("[you] ", "bold cyan"),
        ("(reply) ", "green"),
        "more than once\n",
        ("[bot] ", "bold magenta"),
        ("done · codex · 8s\n", "dim"),
        ("      codex resume ", "dim"),
        ("abc123", "cyan"),
    )


def render_persona_preview(ui: UI) -> None:
    panel_width = 40
    workspace_layout = Group(
        render_persona_tabs(),
        render_workspace_preview(),
    )
    assistant_panel = Panel(
        render_assistant_preview(),
        title=Text("assistant", style="bold"),
        subtitle="ongoing chat (recommended)",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1),
        width=panel_width,
    )
    handoff_panel = Panel(
        render_handoff_preview(),
        title=Text("handoff", style="bold"),
        subtitle="reply · terminal resume",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 1),
        width=panel_width,
    )
    workspace_panel = Panel(
        workspace_layout,
        title=Text("workspace", style="bold"),
        subtitle="project/branch workspaces",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1),
        width=panel_width,
    )
    ui.print(
        Columns(
            [assistant_panel, workspace_panel, handoff_panel],
            expand=False,
            equal=True,
            padding=(0, 2),
        ),
        markup=False,
    )


async def prompt_persona(ui: UI) -> Persona | None:
    render_persona_preview(ui)
    ui.print("")
    return cast(
        Persona,
        await ui.select(
            "how will you use takopi?",
            choices=[
                ("assistant (ongoing chat, /new to reset)", "assistant"),
                ("workspace (projects + branches, i'll set those up)", "workspace"),
                ("handoff (reply to continue, terminal resume)", "handoff"),
            ],
        ),
    )


async def validate_topics_onboarding(
    token: str,
    chat_id: int,
    scope: TopicScope,
    project_chat_ids: tuple[int, ...],
) -> ConfigError | None:
    bot = TelegramClient(token)
    try:
        settings = TelegramTopicsSettings(enabled=True, scope=scope)
        await _validate_topics_setup_for(
            bot=bot,
            topics=settings,
            chat_id=chat_id,
            project_chat_ids=project_chat_ids,
        )
        return None
    except ConfigError as exc:
        return exc
    except Exception as exc:  # noqa: BLE001
        return ConfigError(f"topics validation failed: {exc}")
    finally:
        await bot.close()


@contextmanager
def suppress_logging():
    with suppress_logs():
        yield


async def confirm_prompt(message: str, *, default: bool = True) -> bool | None:
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
    def other(_event):
        return None

    question = Question(
        PromptSession(get_prompt_tokens, key_bindings=bindings, style=merged_style).app
    )
    return await question.ask_async()


class InteractiveUI:
    def __init__(self, console: Console) -> None:
        self._console = console

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
        return await confirm_prompt(prompt, default=default)

    async def select(self, prompt: str, choices: list[tuple[str, Any]]) -> Any | None:
        return await questionary.select(
            prompt,
            choices=[
                questionary.Choice(label, value=value) for label, value in choices
            ],
            instruction="(use arrow keys)",
        ).ask_async()

    async def password(self, prompt: str) -> str | None:
        return await questionary.password(prompt).ask_async()


class LiveServices:
    async def get_bot_info(self, token: str) -> User | None:
        return await get_bot_info(token)

    async def wait_for_chat(self, token: str) -> ChatInfo:
        return await wait_for_chat(token)

    async def validate_topics(
        self, token: str, chat_id: int, scope: TopicScope
    ) -> ConfigError | None:
        return await validate_topics_onboarding(token, chat_id, scope, ())

    def list_engines(self) -> list[tuple[str, bool, str | None]]:
        rows: list[tuple[str, bool, str | None]] = []
        for backend in list_backends():
            cmd = backend.cli_cmd or backend.id
            installed = shutil.which(cmd) is not None
            rows.append((backend.id, installed, backend.install_cmd))
        return rows

    def read_config(self, path: Path) -> dict[str, Any]:
        return read_config(path)

    def write_config(self, path: Path, data: dict[str, Any]) -> None:
        write_config(data, path)


async def prompt_token(ui: UI, svc: Services) -> tuple[str, User]:
    while True:
        ui.print("")
        token = require_value(await ui.password("paste your bot token:"))
        token = token.strip()
        if not token:
            ui.print("  token cannot be empty")
            continue
        ui.print("  validating...")
        info = await svc.get_bot_info(token)
        if info:
            if info.username:
                ui.print(f"  connected to @{info.username}")
            else:
                name = info.first_name or "your bot"
                ui.print(f"  connected to {name}")
            return token, info
        ui.print("  failed to connect, check the token and try again")
        ui.print("")
        retry = await ui.confirm("try again?", default=True)
        if not retry:
            raise OnboardingCancelled()


def build_transport_patch(state: OnboardingState, *, bot_token: str) -> dict[str, Any]:
    if state.chat is None:
        raise RuntimeError("onboarding state missing chat")
    if state.session_mode is None:
        raise RuntimeError("onboarding state missing session mode")
    if state.show_resume_line is None:
        raise RuntimeError("onboarding state missing resume choice")
    return {
        "bot_token": bot_token,
        "chat_id": state.chat.chat_id,
        "session_mode": state.session_mode,
        "show_resume_line": state.show_resume_line,
        "topics": {
            "enabled": state.topics_enabled,
            "scope": state.topics_scope,
        },
    }


def build_config_patch(state: OnboardingState, *, bot_token: str) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "transport": "telegram",
        "transports": {"telegram": build_transport_patch(state, bot_token=bot_token)},
    }
    if state.default_engine is not None:
        patch["default_engine"] = state.default_engine
    return patch


def merge_config(
    existing: dict[str, Any],
    patch: dict[str, Any],
    *,
    config_path: Path,
) -> dict[str, Any]:
    merged = dict(existing)
    if "default_engine" in patch:
        merged["default_engine"] = patch["default_engine"]
    merged["transport"] = patch["transport"]
    transports = ensure_table(merged, "transports", config_path=config_path)
    telegram = ensure_table(
        transports,
        "telegram",
        config_path=config_path,
        label="transports.telegram",
    )
    telegram_patch = patch["transports"]["telegram"]
    telegram["bot_token"] = telegram_patch["bot_token"]
    telegram["chat_id"] = telegram_patch["chat_id"]
    telegram["session_mode"] = telegram_patch["session_mode"]
    telegram["show_resume_line"] = telegram_patch["show_resume_line"]
    topics = ensure_table(
        telegram,
        "topics",
        config_path=config_path,
        label="transports.telegram.topics",
    )
    topics_patch = telegram_patch["topics"]
    topics["enabled"] = topics_patch["enabled"]
    topics["scope"] = topics_patch["scope"]
    merged.pop("bot_token", None)
    merged.pop("chat_id", None)
    return merged


async def capture_chat(
    ui: UI,
    svc: Services,
    state: OnboardingState,
    *,
    prompt: Text | None = None,
) -> None:
    if state.token is None:
        raise RuntimeError("onboarding state missing token")
    if prompt is not None:
        ui.print(prompt, markup=False)
    ui.print("  waiting for message...")
    try:
        chat = await svc.wait_for_chat(state.token)
    except KeyboardInterrupt as exc:
        ui.print("  cancelled")
        raise OnboardingCancelled() from exc
    if chat is None:
        ui.print("  cancelled")
        raise OnboardingCancelled()
    if chat.is_group or chat.chat_type == "channel":
        ui.print(f"  got chat_id {chat.chat_id} for {chat.kind}")
    else:
        ui.print(f"  got chat_id {chat.chat_id} for {chat.display} ({chat.kind})")
    state.chat = chat


async def step_token_and_bot(ui: UI, svc: Services, state: OnboardingState) -> None:
    have_token = require_value(
        await ui.confirm("do you already have a bot token from @BotFather?")
    )
    if not have_token:
        ui.print(render_botfather_instructions(), markup=False)
    else:
        ui.print("  token looks like 123456789:ABCdef...")
    token, info = await prompt_token(ui, svc)
    state.token = token
    state.bot_username = info.username
    state.bot_name = info.first_name


async def step_persona(ui: UI, _svc: Services, state: OnboardingState) -> None:
    persona = await prompt_persona(ui)
    state.persona = require_value(persona)
    if state.persona == "workspace":
        state.session_mode = "chat"
        state.topics_enabled = True
        state.topics_scope = "auto"
        state.show_resume_line = False
        return
    if state.persona == "assistant":
        state.session_mode = "chat"
        state.topics_enabled = False
        state.topics_scope = "auto"
        state.show_resume_line = False
        return
    state.session_mode = "stateless"
    state.topics_enabled = False
    state.topics_scope = "auto"
    state.show_resume_line = True


async def step_capture_chat(ui: UI, svc: Services, state: OnboardingState) -> None:
    if state.persona is None:
        raise RuntimeError("onboarding state missing persona")
    if state.persona == "workspace":
        await capture_chat(
            ui,
            svc,
            state,
            prompt=render_topics_group_instructions(state.bot_ref),
        )
        if state.token is None:
            raise RuntimeError("onboarding state missing token")
        if state.chat is None:
            raise RuntimeError("onboarding state missing chat")
        while True:
            ui.print("  validating topics setup...")
            issue = await svc.validate_topics(
                state.token,
                state.chat.chat_id,
                state.topics_scope,
            )
            if issue is None:
                break
            ui.print(render_topics_validation_warning(issue), markup=False)
            ui.print("")
            choice = await ui.select(
                "how to proceed?",
                choices=[
                    ("retry validation", "retry"),
                    ("switch to assistant mode", "assistant"),
                ],
            )
            if choice is None:
                raise OnboardingCancelled()
            if choice == "assistant":
                state.persona = "assistant"
                state.topics_enabled = False
                state.topics_scope = "auto"
                break
        return
    await capture_chat(
        ui,
        svc,
        state,
        prompt=render_private_chat_instructions(state.bot_ref),
    )


async def step_default_engine(ui: UI, svc: Services, state: OnboardingState) -> None:
    ui.print("takopi runs these engines on your computer. switch anytime with /agent.")
    rows = svc.list_engines()
    render_engine_table(ui, rows)
    installed_ids = [engine_id for engine_id, installed, _ in rows if installed]

    if installed_ids:
        ui.print("")
        default_engine = await ui.select(
            "choose default engine:",
            choices=[(engine_id, engine_id) for engine_id in installed_ids],
        )
        state.default_engine = require_value(default_engine)
        return

    ui.print("no engines found. install one and rerun --onboard.")
    ui.print("")
    save_anyway = await ui.confirm("save config anyway?", default=False)
    if not save_anyway:
        raise OnboardingCancelled()


async def step_save_config(ui: UI, svc: Services, state: OnboardingState) -> None:
    save = await ui.confirm(
        f"save config to {display_path(state.config_path)}?",
        default=True,
    )
    if not save:
        raise OnboardingCancelled()

    raw_config: dict[str, Any] = {}
    if state.config_path.exists():
        try:
            raw_config = svc.read_config(state.config_path)
        except ConfigError as exc:
            ui.print(render_config_malformed_warning(exc), markup=False)
            backup = state.config_path.with_suffix(".toml.bak")
            try:
                shutil.copyfile(state.config_path, backup)
            except OSError as copy_exc:
                ui.print(render_backup_failed_warning(copy_exc), markup=False)
            else:
                ui.print(f"  backed up to {display_path(backup)}")
            raw_config = {}
    if state.token is None:
        raise RuntimeError("onboarding state missing token")
    patch = build_config_patch(state, bot_token=state.token)
    merged = merge_config(raw_config, patch, config_path=state.config_path)
    svc.write_config(state.config_path, merged)
    ui.print("")
    ui.print(Text("✓ setup complete. starting takopi...", style="green"))


def always_true(_state: OnboardingState) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class OnboardingStep:
    title: str | None
    number: int | None
    run: Callable[[UI, Services, OnboardingState], Awaitable[None]]
    applies: Callable[[OnboardingState], bool] = always_true


STEPS: list[OnboardingStep] = [
    OnboardingStep("bot token", 1, step_token_and_bot),
    OnboardingStep("pick your workflow", 2, step_persona),
    OnboardingStep("connect chat", 3, step_capture_chat),
    OnboardingStep("default engine", 4, step_default_engine),
    OnboardingStep("save config", 5, step_save_config),
]


async def run_onboarding(ui: UI, svc: Services, state: OnboardingState) -> bool:
    try:
        for step in STEPS:
            if not step.applies(state):
                continue
            if step.title and step.number is not None:
                ui.step(step.title, number=step.number)
            await step.run(ui, svc, state)
    except OnboardingCancelled:
        return False
    return True


async def capture_chat_id(*, token: str | None = None) -> ChatInfo | None:
    ui = InteractiveUI(Console())
    svc = LiveServices()
    state = OnboardingState(config_path=HOME_CONFIG_PATH, force=False)
    with suppress_logging():
        try:
            if token is not None:
                token = token.strip()
                if not token:
                    ui.print("  token cannot be empty")
                    return None
                ui.print("  validating...")
                info = await svc.get_bot_info(token)
                if not info:
                    ui.print("  failed to connect, check the token and try again")
                    return None
                state.token = token
                state.bot_username = info.username
                state.bot_name = info.first_name
            else:
                token, info = await prompt_token(ui, svc)
                state.token = token
                state.bot_username = info.username
                state.bot_name = info.first_name

            await capture_chat(
                ui,
                svc,
                state,
                prompt=render_generic_capture_prompt(state.bot_ref),
            )
            return state.chat
        except OnboardingCancelled:
            return None


async def interactive_setup(*, force: bool) -> bool:
    ui = InteractiveUI(Console())
    svc = LiveServices()
    state = OnboardingState(config_path=HOME_CONFIG_PATH, force=force)

    if state.config_path.exists() and not force:
        ui.print(
            f"config already exists at {display_path(state.config_path)}. "
            "use --onboard to reconfigure."
        )
        return True

    if state.config_path.exists() and force:
        overwrite = await ui.confirm(
            f"update existing config at {display_path(state.config_path)}?",
            default=False,
        )
        if not overwrite:
            return False

    with suppress_logging():
        return await run_onboarding(ui, svc, state)


def debug_onboarding_paths(console: Console | None = None) -> None:
    console = console or Console()
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("#", justify="right", style="dim")
    table.add_column("persona")
    table.add_column("session")
    table.add_column("topics")
    table.add_column("resume footer")
    table.add_column("topics check")
    table.add_column("engines")
    table.add_column("save anyway")
    table.add_column("save config")
    table.add_column("outcome")

    engine_paths: list[tuple[bool, bool | None, tuple[bool | None, ...]]] = [
        (True, None, (True, False)),
        (False, False, (None,)),
        (False, True, (True, False)),
    ]

    path_count = 0
    personas = {
        "workspace": ("chat", True, "hide"),
        "assistant": ("chat", False, "hide"),
        "handoff": ("stateless", False, "show (fixed)"),
    }
    for persona, (session_mode, topics_enabled, resume_label) in personas.items():
        topics_label = "on" if topics_enabled else "off"
        topics_check = "run" if topics_enabled else "skip"
        for agents_found, save_anyway, save_configs in engine_paths:
            for save_config in save_configs:
                path_count += 1
                agents_label = "found" if agents_found else "none"
                save_anyway_label = format_bool(save_anyway)
                save_config_label = format_bool(save_config)
                outcome = "saved" if save_config else "exit"
                table.add_row(
                    str(path_count),
                    persona,
                    session_mode,
                    topics_label,
                    resume_label,
                    topics_check,
                    agents_label,
                    save_anyway_label,
                    save_config_label,
                    outcome,
                )

    console.print(f"onboarding paths ({path_count})", markup=False)
    console.print(
        "assumes config is missing or --onboard was confirmed; "
        "cancellations/timeouts are omitted.",
        markup=False,
    )
    console.print("")
    console.print(table)


def format_bool(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "no"
