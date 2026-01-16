from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from takopi import cli
from takopi.backends import EngineBackend, SetupIssue
from takopi.settings import TakopiSettings
from takopi.transports import SetupResult


@dataclass
class _DummyLock:
    released: bool = False

    def release(self) -> None:
        self.released = True


class _FakeTransport:
    id = "fake"
    description = "fake transport"

    def __init__(self, setup: SetupResult) -> None:
        self._setup = setup
        self.check_calls: list[tuple[object, str | None]] = []
        self.lock_calls: list[tuple[object, Path]] = []
        self.build_calls: list[dict[str, object]] = []

    def check_setup(self, engine_backend, *, transport_override=None) -> SetupResult:
        self.check_calls.append((engine_backend, transport_override))
        return self._setup

    def interactive_setup(self, *, force: bool) -> bool:
        _ = force
        return True

    def lock_token(self, *, transport_config: object, _config_path: Path) -> str | None:
        self.lock_calls.append((transport_config, _config_path))
        return "lock"

    def build_and_run(
        self,
        *,
        transport_config: object,
        config_path: Path,
        runtime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        self.build_calls.append(
            {
                "transport_config": transport_config,
                "config_path": config_path,
                "runtime": runtime,
                "final_notify": final_notify,
                "default_engine_override": default_engine_override,
            }
        )


def _engine_backend() -> EngineBackend:
    return EngineBackend(id="codex", build_runner=lambda _cfg, _path: None)


def _settings() -> TakopiSettings:
    return TakopiSettings.model_validate(
        {
            "transport": "telegram",
            "transports": {"telegram": {"bot_token": "token", "chat_id": 123}},
        }
    )


def test_run_auto_router_success_releases_lock(monkeypatch, tmp_path: Path) -> None:
    setup = SetupResult(issues=[], config_path=tmp_path / "takopi.toml")
    transport = _FakeTransport(setup)
    engine_backend = _engine_backend()
    config_path = tmp_path / "takopi.toml"

    monkeypatch.setattr(
        cli,
        "_resolve_setup_engine",
        lambda _override: (None, None, None, "codex", engine_backend),
    )
    monkeypatch.setattr(cli, "_resolve_transport_id", lambda _override: "wire")
    monkeypatch.setattr(cli, "get_transport", lambda _id, allowlist=None: transport)
    monkeypatch.setattr(cli, "load_settings", lambda: (_settings(), config_path))
    monkeypatch.setattr(cli, "setup_logging", lambda **_kwargs: None)

    spec_calls: dict[str, object] = {}

    class _Spec:
        def to_runtime(self, *, config_path: Path):
            spec_calls["runtime_config_path"] = config_path
            return "runtime"

    def _build_runtime_spec(**kwargs):
        spec_calls.update(kwargs)
        return _Spec()

    monkeypatch.setattr(cli, "build_runtime_spec", _build_runtime_spec)
    lock = _DummyLock()
    monkeypatch.setattr(cli, "acquire_config_lock", lambda _path, _token: lock)

    cli._run_auto_router(
        default_engine_override=None,
        transport_override="wire",
        final_notify=True,
        debug=False,
        onboard=False,
    )

    assert transport.build_calls
    assert lock.released is True
    assert spec_calls["reserved"] == cli.RESERVED_CHAT_COMMANDS
    assert transport.lock_calls[0][0] == {}


def test_run_auto_router_requires_tty_for_onboard(monkeypatch, tmp_path: Path) -> None:
    setup = SetupResult(issues=[], config_path=tmp_path / "takopi.toml")
    transport = _FakeTransport(setup)

    monkeypatch.setattr(
        cli,
        "_resolve_setup_engine",
        lambda _override: (None, None, None, "codex", _engine_backend()),
    )
    monkeypatch.setattr(cli, "_resolve_transport_id", lambda _override: "fake")
    monkeypatch.setattr(cli, "get_transport", lambda _id, allowlist=None: transport)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr(cli, "setup_logging", lambda **_kwargs: None)

    with pytest.raises(cli.typer.Exit) as exc:
        cli._run_auto_router(
            default_engine_override=None,
            transport_override=None,
            final_notify=True,
            debug=False,
            onboard=True,
        )

    assert exc.value.exit_code == 1
    assert not transport.build_calls


def test_run_auto_router_missing_config_noninteractive(
    monkeypatch, tmp_path: Path
) -> None:
    setup = SetupResult(
        issues=[SetupIssue(title="create a config", lines=())],
        config_path=tmp_path / "missing.toml",
    )
    transport = _FakeTransport(setup)

    monkeypatch.setattr(
        cli,
        "_resolve_setup_engine",
        lambda _override: (None, None, None, "codex", _engine_backend()),
    )
    monkeypatch.setattr(cli, "_resolve_transport_id", lambda _override: "fake")
    monkeypatch.setattr(cli, "get_transport", lambda _id, allowlist=None: transport)
    monkeypatch.setattr(cli, "_should_run_interactive", lambda: False)
    monkeypatch.setattr(cli, "setup_logging", lambda **_kwargs: None)

    with pytest.raises(cli.typer.Exit) as exc:
        cli._run_auto_router(
            default_engine_override=None,
            transport_override=None,
            final_notify=True,
            debug=False,
            onboard=False,
        )

    assert exc.value.exit_code == 1
    assert not transport.build_calls
