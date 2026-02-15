from dataclasses import replace
from pathlib import Path

import pytest

from takopi.config import ProjectsConfig
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram.bridge import TelegramBridgeConfig
from takopi.telegram.chat_prefs import ChatPrefsStore, resolve_prefs_path
from takopi.telegram.commands.mode import _handle_mode_command
from takopi.telegram.loop import _active_mode_shortcuts
from takopi.telegram.loop import run_main_loop
from takopi.telegram.types import TelegramIncomingMessage
from takopi.transport_runtime import TransportRuntime
from tests.telegram_fakes import FakeTransport, make_cfg


def _msg(
    text: str,
    *,
    chat_id: int = 123,
    message_id: int = 10,
    sender_id: int | None = 42,
    chat_type: str | None = "private",
    thread_id: int | None = None,
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=sender_id,
        chat_type=chat_type,
        thread_id=thread_id,
    )


def _last_text(transport: FakeTransport) -> str:
    assert transport.send_calls
    return transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_mode_show_and_set_clear(tmp_path: Path) -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        mode_supported_engines=frozenset({"codex"}),
        mode_known_modes={"codex": ("build", "plan")},
    )
    prefs = ChatPrefsStore(tmp_path / "prefs.json")
    msg = _msg("/mode")

    await _handle_mode_command(
        cfg,
        msg,
        args_text="",
        ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )
    text = _last_text(transport)
    assert "engine: codex" in text
    assert "mode: default (no override)" in text
    assert "available modes: build, plan" in text

    await _handle_mode_command(
        cfg,
        replace(msg, text="/mode plan", message_id=11),
        args_text="plan",
        ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )
    override = await prefs.get_engine_override(msg.chat_id, "codex")
    assert override is not None
    assert override.mode == "plan"
    assert "chat mode override set to plan for codex." in _last_text(transport)

    await _handle_mode_command(
        cfg,
        replace(msg, text="/mode clear", message_id=12),
        args_text="clear",
        ambient_context=None,
        topic_store=None,
        chat_prefs=prefs,
    )
    override = await prefs.get_engine_override(msg.chat_id, "codex")
    assert override is None
    assert "chat mode override cleared." in _last_text(transport)


@pytest.mark.anyio
async def test_mode_shortcut_with_text_sets_override_and_runs(tmp_path: Path) -> None:
    transport = FakeTransport()
    base_cfg = make_cfg(transport)
    runner = ScriptRunner([Return(answer="ok")], engine="codex")
    router = AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )
    config_path = tmp_path / "takopi.toml"
    config_path.write_text('transport = "telegram"\n', encoding="utf-8")
    runtime = TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={}, default_project=None),
        config_path=config_path,
    )
    cfg = TelegramBridgeConfig(
        bot=base_cfg.bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=base_cfg.exec_cfg,
        mode_supported_engines=frozenset({"codex"}),
        mode_known_modes={"codex": ("build", "plan")},
        mode_shortcuts=("plan",),
    )

    async def poller(_cfg: TelegramBridgeConfig):
        yield _msg("/plan hello", chat_type="private")

    await run_main_loop(cfg, poller)

    assert runner.calls
    assert runner.calls[0][0] == "hello"
    prefs = ChatPrefsStore(resolve_prefs_path(config_path))
    override = await prefs.get_engine_override(123, "codex")
    assert override is not None
    assert override.mode == "plan"


def test_active_mode_shortcuts_skip_collisions() -> None:
    shortcuts = _active_mode_shortcuts(
        mode_shortcuts=("agent", "plan", "ctx", "build"),
        reserved_commands={"agent", "ctx", "codex"},
        command_ids={"build"},
    )
    assert shortcuts == ("plan",)
