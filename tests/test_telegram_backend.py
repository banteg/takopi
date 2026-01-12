from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from takopi.config import ProjectsConfig
from takopi.model import EngineId
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.settings import (
    TelegramFilesSettings,
    TelegramTopicsSettings,
    TelegramTransportSettings,
)
from takopi.telegram import backend as telegram_backend
from takopi.transport_runtime import TransportRuntime


def test_build_startup_message_includes_missing_engines(tmp_path: Path) -> None:
    codex = EngineId("codex")
    pi = EngineId("pi")
    runner = ScriptRunner([Return(answer="ok")], engine=codex)
    missing = ScriptRunner([Return(answer="ok")], engine=pi)
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex, runner=runner),
            RunnerEntry(
                engine=pi,
                runner=missing,
                status="missing_cli",
                issue="missing",
            ),
        ],
        default_engine=codex,
    )
    runtime = TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={}, default_project=None),
        watch_config=True,
    )

    message = telegram_backend._build_startup_message(
        runtime, startup_pwd=str(tmp_path)
    )

    assert "takopi is ready" in message
    assert "agents: `codex (not installed: pi)`" in message
    assert "projects: `none`" in message


def test_build_startup_message_surfaces_unavailable_engine_reasons(
    tmp_path: Path,
) -> None:
    codex = EngineId("codex")
    pi = EngineId("pi")
    claude = EngineId("claude")
    runner = ScriptRunner([Return(answer="ok")], engine=codex)
    bad_cfg = ScriptRunner([Return(answer="ok")], engine=pi)
    load_err = ScriptRunner([Return(answer="ok")], engine=claude)

    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex, runner=runner),
            RunnerEntry(engine=pi, runner=bad_cfg, status="bad_config", issue="bad"),
            RunnerEntry(
                engine=claude,
                runner=load_err,
                status="load_error",
                issue="failed",
            ),
        ],
        default_engine=codex,
    )
    runtime = TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={}, default_project=None),
        watch_config=True,
    )

    message = telegram_backend._build_startup_message(
        runtime, startup_pwd=str(tmp_path)
    )

    assert "agents: `codex" in message
    assert "misconfigured: pi" in message
    assert "failed to load: claude" in message


def test_telegram_backend_build_and_run_wires_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'watch_config = true\ntransport = "telegram"\n\n'
        "[transports.telegram]\n"
        'bot_token = "token"\n'
        "chat_id = 321\n",
        encoding="utf-8",
    )

    codex = EngineId("codex")
    runner = ScriptRunner([Return(answer="ok")], engine=codex)
    router = AutoRouter(
        entries=[RunnerEntry(engine=codex, runner=runner)],
        default_engine=codex,
    )
    runtime = TransportRuntime(
        router=router,
        projects=ProjectsConfig(projects={}, default_project=None),
        watch_config=True,
    )

    captured: dict[str, Any] = {}

    async def fake_run_main_loop(cfg, **kwargs) -> None:
        captured["cfg"] = cfg
        captured["kwargs"] = kwargs

    class _FakeClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def close(self) -> None:
            return None

    monkeypatch.setattr(telegram_backend, "run_main_loop", fake_run_main_loop)
    monkeypatch.setattr(telegram_backend, "TelegramClient", _FakeClient)

    transport_config = TelegramTransportSettings(
        bot_token="token",
        chat_id=321,
        voice_transcription=True,
        voice_max_bytes=1234,
        voice_transcription_model="whisper-1",
        files=TelegramFilesSettings(enabled=True, allowed_user_ids=[1, 2]),
        topics=TelegramTopicsSettings(enabled=True, scope="main"),
    )

    telegram_backend.TelegramBackend().build_and_run(
        transport_config=transport_config,
        config_path=config_path,
        runtime=runtime,
        final_notify=False,
        default_engine_override=None,
    )

    cfg = captured["cfg"]
    kwargs = captured["kwargs"]
    assert cfg.chat_id == 321
    assert cfg.voice_transcription is True
    assert cfg.voice_max_bytes == 1234
    assert cfg.voice_transcription_model == "whisper-1"
    assert cfg.files.enabled is True
    assert cfg.files.allowed_user_ids == [1, 2]
    assert cfg.topics.enabled is True
    assert cfg.bot.token == "token"
    assert kwargs["watch_config"] is True
    assert kwargs["transport_id"] == "telegram"


def test_telegram_files_settings_defaults() -> None:
    cfg = TelegramFilesSettings()

    assert cfg.enabled is False
    assert cfg.auto_put is True
    assert cfg.uploads_dir == "incoming"
    assert cfg.allowed_user_ids == []
