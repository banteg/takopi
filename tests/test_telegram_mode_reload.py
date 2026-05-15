from types import SimpleNamespace
from pathlib import Path

import anyio
import pytest

from takopi.agent_modes import ModeDiscoveryResult
from takopi.config import ProjectsConfig
from takopi.markdown import MarkdownPresenter
from takopi.router import AutoRouter, RunnerEntry
from takopi.runner_bridge import ExecBridgeConfig
from takopi.runners.mock import Return, ScriptRunner
from takopi.settings import TelegramTransportSettings, TakopiSettings
from takopi.telegram.bridge import TelegramBridgeConfig
from takopi.telegram.loop import run_main_loop
import takopi.telegram.loop as telegram_loop
from takopi.transport_runtime import TransportRuntime
from tests.telegram_fakes import FakeBot, FakeTransport


@pytest.mark.anyio
async def test_watch_config_refreshes_mode_shortcuts(
    monkeypatch, tmp_path: Path
) -> None:
    transport = FakeTransport()
    bot = FakeBot()
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
        bot=bot,
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=ExecBridgeConfig(
            transport=transport,
            presenter=MarkdownPresenter(),
            final_notify=True,
        ),
        mode_shortcuts=(),
    )

    reloaded = anyio.Event()

    def fake_discover(self, *, timeout_s: float) -> ModeDiscoveryResult:
        _ = self, timeout_s
        return ModeDiscoveryResult(
            supports_agent=frozenset({"codex"}),
            known_modes={"codex": ("plan", "build")},
            shortcut_modes=("plan",),
        )

    monkeypatch.setattr(TransportRuntime, "discover_agent_modes", fake_discover)

    async def fake_watch_config_changes(**kwargs) -> None:
        on_reload = kwargs["on_reload"]
        settings = TakopiSettings.model_validate(
            {
                "transport": "telegram",
                "transports": {
                    "telegram": {
                        "bot_token": "token",
                        "chat_id": 123,
                        "mode_discovery_timeout_s": 8.0,
                    }
                },
            }
        )
        await on_reload(
            SimpleNamespace(
                settings=settings,
                runtime_spec=None,
                config_path=config_path,
            )
        )
        reloaded.set()

    monkeypatch.setattr(
        telegram_loop, "watch_config_changes", fake_watch_config_changes
    )

    async def poller(_cfg: TelegramBridgeConfig):
        await reloaded.wait()
        if False:
            yield

    await run_main_loop(
        cfg,
        poller,
        watch_config=True,
        transport_id="telegram",
        transport_config=TelegramTransportSettings(bot_token="token", chat_id=123),
    )

    assert len(bot.command_calls) >= 2
    latest_commands = bot.command_calls[-1]["commands"]
    assert any(command["command"] == "plan" for command in latest_commands)
