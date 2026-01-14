from __future__ import annotations

from contextlib import contextmanager
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from takopi.backends import EngineBackend
from takopi.config import dump_toml
from takopi.telegram import onboarding as ob


def _section(console: Console, title: str) -> None:
    console.print("")
    console.print(f"=== {title} ===", markup=False)


def _render_confirm(console: Console, prompt: str) -> None:
    console.print(f"? {prompt} (yes/no)", markup=False)


def _render_password(console: Console, prompt: str) -> None:
    console.print(f"? {prompt} {'*' * 28}", markup=False)


def _render_select(console: Console, prompt: str, choices: list[str]) -> None:
    console.print(f"? {prompt} (Use arrow keys)", markup=False)
    for index, choice in enumerate(choices):
        marker = ">" if index == 0 else " "
        console.print(f"{marker} {choice}", markup=False)


@contextmanager
def _patched_select(console: Console):
    original = ob.questionary.select

    def _select(text, choices, **_kwargs):
        rendered: list[str] = []
        for choice in choices:
            label = getattr(choice, "title", None)
            rendered.append(label if label is not None else str(choice))
        _render_select(console, text, rendered)

        class _Dummy:
            def ask(self):
                return None

        return _Dummy()

    ob.questionary.select = _select
    try:
        yield
    finally:
        ob.questionary.select = original


@contextmanager
def _patched_backends(console: Console, installed_ids: set[str]):
    original_list = ob.list_backends
    original_which = ob.shutil.which

    def _noop_build(_config, _path):
        raise RuntimeError("not used")

    backends = [
        EngineBackend(
            id="codex",
            build_runner=_noop_build,
            install_cmd="brew install codex",
        ),
        EngineBackend(
            id="claude",
            build_runner=_noop_build,
            install_cmd="brew install claude",
        ),
        EngineBackend(
            id="opencode",
            build_runner=_noop_build,
            install_cmd="brew install opencode",
        ),
    ]

    def _list_backends():
        return backends

    def _which(cmd: str):
        return f"/usr/local/bin/{cmd}" if cmd in installed_ids else None

    ob.list_backends = _list_backends
    ob.shutil.which = _which
    try:
        yield
    finally:
        ob.list_backends = original_list
        ob.shutil.which = original_which


def _render_step(console: Console, number: int, title: str) -> None:
    console.print(Text(f"step {number}: {title}", style="bold yellow"))


def _render_welcome(console: Console) -> None:
    panel = Panel(
        f"let's set up your telegram bot.\nwe'll write {ob._display_path(ob.HOME_CONFIG_PATH)}.",
        title="welcome to takopi!",
        border_style="yellow",
        padding=(1, 2),
        expand=False,
    )
    console.print(panel)


def _render_step_1(console: Console) -> None:
    _render_step(console, 1, "telegram bot setup")
    _render_confirm(console, "do you already have a bot token from @BotFather?")


def _render_step_1_no_token(console: Console) -> None:
    console.print("  1. open telegram and message @BotFather", markup=False)
    console.print("  2. send /newbot and follow the prompts", markup=False)
    console.print("  3. copy the token (looks like 123456789:ABCdef...)", markup=False)
    console.print("", markup=False)
    console.print(
        "  keep this token secret - it grants full control of your bot.",
        markup=False,
    )


def _render_token_validation(console: Console, *, success: bool) -> None:
    _render_password(console, "paste your bot token:")
    console.print("  validating...", markup=False)
    if success:
        console.print("  connected to @bunny_agent_bot", markup=False)
    else:
        console.print("  failed to connect, check the token and try again", markup=False)
        _render_confirm(console, "try again?")


def _render_chat_capture(
    console: Console,
    *,
    chat_id: int,
    kind: str,
    display: str,
) -> None:
    console.print("", markup=False)
    console.print(
        "  send /start to @bunny_agent_bot in the chat you want takopi to use "
        "(dm or group)",
        markup=False,
    )
    console.print("  waiting...", markup=False)
    console.print(f"  got chat_id {chat_id} ({kind}) from {display}", markup=False)


def _render_conversation_style(console: Console) -> None:
    _render_step(console, 2, "threads (how follow-ups work)")
    with _patched_select(console):
        ob._prompt_session_mode(console)


def _render_topics(console: Console, label: str, chat: ob.ChatInfo) -> None:
    _section(console, label)
    _render_step(console, 3, "topics & resume footer")
    with _patched_select(console):
        ob._prompt_topics(console, chat)


def _render_resume_lines(console: Console) -> None:
    _section(console, "resume footer prompt")
    with _patched_select(console):
        ob._prompt_resume_lines(console)


def _render_topics_validation_warning(console: Console) -> None:
    _section(console, "topics validation warning")
    console.print("  validating topics setup...", markup=False)
    console.print(
        "[yellow]warning:[/] topics aren't ready in this chat: missing admin rights"
    )
    console.print(
        "  fix:\n"
        "  - promote @bunny_agent_bot to admin\n"
        "  - enable \"manage topics\"\n"
        "  - rerun takopi --onboard"
    )
    _render_confirm(console, "disable topics for now? (recommended)")


def _render_engine_table(console: Console, installed_ids: set[str]) -> None:
    _render_step(console, 4, "default agent")
    console.print(
        "takopi runs one of these agent CLIs on your machine. "
        "you can switch per message later.",
        markup=False,
    )
    with _patched_backends(console, installed_ids):
        ob._render_engine_table(console)


def _render_choose_default_agent(console: Console) -> None:
    _render_select(console, "choose default agent:", ["codex", "claude", "opencode"])


def _render_no_agents(console: Console) -> None:
    console.print("no agents found on PATH. install one to continue.", markup=False)
    _render_confirm(console, "save config anyway?")


def _render_config_preview(
    console: Console,
    *,
    session_mode: str,
    topics_enabled: bool,
    topics_scope: str,
    show_resume_line: bool,
    default_engine: str | None,
) -> None:
    preview_config: dict[str, object] = {}
    if default_engine is not None:
        preview_config["default_engine"] = default_engine
    preview_config["transport"] = "telegram"
    preview_config["transports"] = {
        "telegram": {
            "bot_token": ob.mask_token("123456789:ABCdef"),
            "chat_id": 462722,
            "session_mode": session_mode,
            "show_resume_line": show_resume_line,
            "topics": {
                "enabled": topics_enabled,
                "scope": topics_scope,
            },
        }
    }
    config_preview = dump_toml(preview_config).rstrip()
    console.print("", markup=False)
    console.print(Text("step 5: save configuration", style="bold yellow"))
    console.print("", markup=False)
    console.print(f"  {ob._display_path(ob.HOME_CONFIG_PATH)}\n", markup=False)
    for line in config_preview.splitlines():
        console.print(f"  {line}", markup=False)
    console.print("", markup=False)
    console.print("  note: your bot token will be saved in plain text.", markup=False)


def _render_save_prompt(console: Console) -> None:
    _render_confirm(
        console,
        f"save this config to {ob._display_path(ob.HOME_CONFIG_PATH)}?",
    )


def _render_save_success(console: Console) -> None:
    console.print(f"  config saved to {ob._display_path(ob.HOME_CONFIG_PATH)}")
    console.print("  sent confirmation message")


def _render_done(console: Console) -> None:
    done_panel = Panel(
        "setup complete. starting takopi...",
        border_style="green",
        padding=(1, 2),
        expand=False,
    )
    console.print("\n", markup=False)
    console.print(done_panel)


def _render_confirmation_messages(console: Console) -> None:
    _section(console, "telegram confirmation messages")
    variants = [
        ("chat", False, True),
        ("chat", True, False),
        ("stateless", False, True),
        ("stateless", True, True),
    ]
    for session_mode, topics_enabled, show_resume_line in variants:
        title = f"mode={session_mode}, topics={topics_enabled}, resume={show_resume_line}"
        console.print("", markup=False)
        console.print(title, style="bold")
        message = ob._build_confirmation_message(
            session_mode=session_mode,
            topics_enabled=topics_enabled,
            show_resume_line=show_resume_line,
        )
        for line in message.splitlines():
            console.print(f"  {line}", markup=False)


def main() -> None:
    console = Console()

    _section(console, "welcome")
    _render_welcome(console)

    _section(console, "step 1: token prompt")
    _render_step_1(console)

    _section(console, "step 1: token instructions (no token)")
    _render_step(console, 1, "telegram bot setup")
    _render_step_1_no_token(console)

    _section(console, "step 1: token validation (success)")
    _render_step(console, 1, "telegram bot setup")
    _render_token_validation(console, success=True)

    _section(console, "step 1: token validation (failure)")
    _render_step(console, 1, "telegram bot setup")
    _render_token_validation(console, success=False)

    _section(console, "step 1: chat capture (private)")
    _render_step(console, 1, "telegram bot setup")
    _render_chat_capture(
        console,
        chat_id=462722,
        kind="private chat",
        display="@banteg",
    )
    _section(console, "step 1: chat capture (group)")
    _render_step(console, 1, "telegram bot setup")
    _render_chat_capture(
        console,
        chat_id=-1001234567890,
        kind='supergroup "takopi devs"',
        display='group "takopi devs"',
    )

    _section(console, "step 2: threads (how follow-ups work)")
    _render_conversation_style(console)

    private_chat = ob.ChatInfo(
        chat_id=462722,
        username="banteg",
        title=None,
        first_name="Banteg",
        last_name=None,
        chat_type="private",
    )
    group_chat = ob.ChatInfo(
        chat_id=-1001234567890,
        username=None,
        title="takopi devs",
        first_name=None,
        last_name=None,
        chat_type="supergroup",
    )
    _render_topics(console, "step 3: topics prompt (private chat)", private_chat)
    _render_topics(console, "step 3: topics prompt (group chat)", group_chat)
    _section(console, "step 3: topics tip (project chats)")
    console.print("  tip: bind a project chat with:", markup=False)
    console.print("  takopi chat-id --project <alias>", markup=False)

    _section(console, "step 3: resume footer prompt")
    _render_resume_lines(console)
    _section(console, "step 3: reply-to-continue note")
    console.print(
        "  reply-to-continue requires resume lines. we'll keep them on.",
        markup=False,
    )
    _render_topics_validation_warning(console)

    _section(console, "step 4: agents found")
    _render_engine_table(console, {"codex", "claude"})
    _render_choose_default_agent(console)

    _section(console, "step 4: no agents")
    _render_engine_table(console, set())
    _render_no_agents(console)

    _section(console, "step 5: config preview (chat, topics off)")
    _render_config_preview(
        console,
        session_mode="chat",
        topics_enabled=False,
        topics_scope="auto",
        show_resume_line=False,
        default_engine="codex",
    )
    _render_save_prompt(console)

    _section(console, "step 5: config preview (stateless, topics on)")
    _render_config_preview(
        console,
        session_mode="stateless",
        topics_enabled=True,
        topics_scope="main",
        show_resume_line=True,
        default_engine=None,
    )
    _render_save_prompt(console)
    _render_save_success(console)

    _section(console, "done")
    _render_done(console)

    _render_confirmation_messages(console)


if __name__ == "__main__":
    main()
