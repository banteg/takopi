from pathlib import Path

import pytest

import takopi.runtime_loader as runtime_loader
from takopi.backends import EngineBackend
from takopi.config import ConfigError
from takopi.runners.mock import Return, ScriptRunner
from takopi.settings import TakopiSettings


def test_build_runtime_spec_minimal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runtime_loader.shutil, "which", lambda _cmd: "/bin/echo")
    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "watch_config": True,
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )

    spec = runtime_loader.build_runtime_spec(
        settings=settings,
        config_path=config_path,
    )

    assert spec.router.default_engine == settings.default_engine
    runtime = spec.to_runtime(config_path=config_path)
    assert runtime.default_engine == settings.default_engine
    assert runtime.watch_config is True


def test_resolve_default_engine_unknown(tmp_path: Path) -> None:
    settings = TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )
    with pytest.raises(ConfigError, match="Unknown default engine"):
        runtime_loader.resolve_default_engine(
            override="unknown",
            settings=settings,
            config_path=tmp_path / "takopi.toml",
            engine_ids=["codex"],
        )


def test_build_runtime_spec_mode_prober_uses_backend_cli_cmd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'default_engine = "weird"\ntransport = "telegram"\n\n'
        "[transports.telegram]\n"
        'bot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )

    settings = TakopiSettings.model_validate(
        {
            "default_engine": "weird",
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )

    runner = ScriptRunner([Return(answer="ok")], engine="weird")
    backend = EngineBackend(
        id="weird",
        build_runner=lambda _cfg, _path: runner,
        cli_cmd="weird-cli",
    )

    monkeypatch.setattr(runtime_loader, "list_backend_ids", lambda **_kwargs: ["weird"])
    monkeypatch.setattr(
        runtime_loader, "get_backend", lambda *_args, **_kwargs: backend
    )
    monkeypatch.setattr(
        runtime_loader.shutil,
        "which",
        lambda cmd: "/bin/ok" if cmd == "weird-cli" else None,
    )

    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = "usage: cli --agent <name>"
        stderr = ""

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return _Result()

    monkeypatch.setattr("takopi.agent_modes.subprocess.run", fake_run)

    spec = runtime_loader.build_runtime_spec(settings=settings, config_path=config_path)
    runtime = spec.to_runtime(config_path=config_path)
    discovered = runtime.discover_agent_modes(timeout_s=1.0)

    assert discovered.supports_agent == frozenset({"weird"})
    assert calls
    assert calls[0][0] == "weird-cli"
