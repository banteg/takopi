from pathlib import Path

import anyio
import pytest

import takopi.config_watch as config_watch
from takopi.config_watch import ConfigReload, config_status, watch_config
from takopi.config import ProjectsConfig
from takopi.router import AutoRouter, RunnerEntry
from takopi.runtime_loader import RuntimeSpec
from takopi.runners.mock import Return, ScriptRunner
from takopi.settings import TakopiSettings
from takopi.transport_runtime import TransportRuntime


def test_config_status_variants(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    status, signature = config_status(missing)
    assert status == "missing"
    assert signature is None

    directory = tmp_path / "config.d"
    directory.mkdir()
    status, signature = config_status(directory)
    assert status == "invalid"
    assert signature is None

    config_file = tmp_path / "takopi.toml"
    config_file.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )
    status, signature = config_status(config_file)
    assert status == "ok"
    assert signature is not None


@pytest.mark.anyio
async def test_watch_config_applies_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text('default_engine = "codex"\n', encoding="utf-8")
    resolved_path = config_path.resolve()

    codex_runner = ScriptRunner([Return(answer="ok")], engine="codex")
    router = AutoRouter(
        entries=[RunnerEntry(engine=codex_runner.engine, runner=codex_runner)],
        default_engine=codex_runner.engine,
    )
    runtime = TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={}, default_project=None),
        config_path=resolved_path,
    )

    pi_runner = ScriptRunner([Return(answer="ok")], engine="pi")
    new_router = AutoRouter(
        entries=[RunnerEntry(engine=pi_runner.engine, runner=pi_runner)],
        default_engine=pi_runner.engine,
    )
    new_spec = RuntimeSpec(
        router=new_router,
        projects=ProjectsConfig(projects={}, default_project=None),
        allowlist=None,
        plugin_configs=None,
    )
    reload = ConfigReload(
        settings=TakopiSettings.model_validate(
            {
                "transport": "telegram",
                "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
            }
        ),
        runtime_spec=new_spec,
        config_path=resolved_path,
    )

    ready = anyio.Event()
    watching = anyio.Event()

    async def fake_awatch(_path: Path):
        watching.set()
        await ready.wait()
        yield {(None, str(resolved_path))}

    monkeypatch.setattr(config_watch, "awatch", fake_awatch)
    monkeypatch.setattr(
        config_watch, "_reload_config", lambda *_args, **_kwargs: reload
    )

    reloaded = anyio.Event()

    async def on_reload(_payload: ConfigReload) -> None:
        reloaded.set()

    async with anyio.create_task_group() as tg:

        async def run_watch() -> None:
            await watch_config(
                config_path=resolved_path,
                runtime=runtime,
                on_reload=on_reload,
            )

        tg.start_soon(run_watch)
        with anyio.fail_after(2):
            await watching.wait()
        config_path.write_text('default_engine = "pi"\n', encoding="utf-8")
        ready.set()
        with anyio.fail_after(2):
            await reloaded.wait()
        tg.cancel_scope.cancel()

    assert runtime.default_engine == "pi"
