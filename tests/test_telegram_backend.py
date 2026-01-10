from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from takopi.config import ConfigError, empty_projects_config
from takopi.model import EngineId
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram import backend as telegram_backend
from takopi.transport_runtime import TransportRuntime


def test_build_startup_message_includes_missing_engines(tmp_path: Path) -> None:
    codex = EngineId("codex")
    pi = EngineId("pi")
    runner = ScriptRunner([Return(answer="ok")], engine=codex)
    missing = ScriptRunner([Return(answer="ok")], engine=pi)
    router = AutoRouter(
        entries=[
            RunnerEntry(engine=codex, runner=runner, available=True),
            RunnerEntry(engine=pi, runner=missing, available=False, issue="missing"),
        ],
        default_engine=codex,
    )
    runtime = TransportRuntime(router=router, projects=empty_projects_config())

    message = telegram_backend._build_startup_message(
        runtime, startup_pwd=str(tmp_path)
    )

    assert "takopi is ready" in message
    assert "agents: `codex (not installed: pi)`" in message
    assert "projects: `none`" in message


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
        entries=[RunnerEntry(engine=codex, runner=runner, available=True)],
        default_engine=codex,
    )
    runtime = TransportRuntime(router=router, projects=empty_projects_config())

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

    transport_config = {
        "bot_token": "token",
        "chat_id": 321,
        "voice_transcription": True,
        "files": {"enabled": True, "allowed_user_ids": [1, 2]},
        "topics": {"enabled": True, "scope": "main"},
    }

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
    assert cfg.voice_transcription is not None
    assert cfg.voice_transcription.enabled is True
    assert cfg.files.enabled is True
    assert cfg.files.allowed_user_ids == frozenset({1, 2})
    assert cfg.topics.enabled is True
    assert cfg.bot.token == "token"
    assert kwargs["watch_config"] is True
    assert kwargs["transport_id"] == "telegram"


def test_build_files_config_rejects_non_dict(tmp_path: Path) -> None:
    config_path = tmp_path / "takopi.toml"
    transport_config: dict[str, object] = {"files": ["nope"]}

    with pytest.raises(ConfigError, match="transports.telegram.files"):
        telegram_backend._build_files_config(
            transport_config,
            config_path=config_path,
        )
