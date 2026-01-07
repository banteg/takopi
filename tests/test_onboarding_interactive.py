from __future__ import annotations

from takopi.telegram import onboarding
from takopi.backends import EngineBackend


def test_mask_token_short() -> None:
    assert onboarding._mask_token("short") == "*****"


def test_mask_token_long() -> None:
    token = "123456789:ABCdefGH"
    masked = onboarding._mask_token(token)
    assert masked.startswith("123456789")
    assert masked.endswith("defGH")
    assert "..." in masked


def test_render_config_escapes() -> None:
    config = onboarding._render_config(
        'token"with\\quote',
        123,
        "codex",
    )
    assert 'default_engine = "codex"' in config
    assert 'transport = "telegram"' in config
    assert "[transports.telegram]" in config
    assert 'bot_token = "token\\"with\\\\quote"' in config
    assert "chat_id = 123" in config
    assert config.endswith("\n")


class _FakeQuestion:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def _queue(values):
    it = iter(values)

    def _make(*_args, **_kwargs):
        return _FakeQuestion(next(it))

    return _make


def _queue_values(values):
    it = iter(values)

    def _next(*_args, **_kwargs):
        return next(it)

    return _next


def test_interactive_setup_skips_when_config_exists(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(onboarding, "HOME_CONFIG_PATH", config_path)
    assert onboarding.interactive_setup(force=False) is True


def test_interactive_setup_writes_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr(onboarding, "HOME_CONFIG_PATH", config_path)

    backend = EngineBackend(id="codex", build_runner=lambda _cfg, _path: None)
    monkeypatch.setattr(onboarding, "list_backends", lambda: [backend])
    monkeypatch.setattr(onboarding.shutil, "which", lambda _cmd: "/usr/bin/codex")

    monkeypatch.setattr(onboarding, "_confirm", _queue_values([True, True]))
    monkeypatch.setattr(
        onboarding.questionary, "password", _queue(["123456789:ABCdef"])
    )
    monkeypatch.setattr(onboarding.questionary, "select", _queue(["codex"]))

    def _fake_run(func, *args, **kwargs):
        if func is onboarding._get_bot_info:
            return {"username": "my_bot"}
        if func is onboarding._wait_for_chat:
            return onboarding.ChatInfo(
                chat_id=123,
                username="alice",
                title=None,
                first_name="Alice",
                last_name=None,
                chat_type="private",
            )
        if func is onboarding._send_confirmation:
            return True
        raise AssertionError(f"unexpected anyio.run target: {func}")

    monkeypatch.setattr(onboarding.anyio, "run", _fake_run)

    assert onboarding.interactive_setup(force=False) is True
    saved = config_path.read_text(encoding="utf-8")
    assert 'transport = "telegram"' in saved
    assert "[transports.telegram]" in saved
    assert 'bot_token = "123456789:ABCdef"' in saved
    assert "chat_id = 123" in saved
    assert 'default_engine = "codex"' in saved


def test_interactive_setup_preserves_projects(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'default_project = "z80"\n\n[projects.z80]\npath = "/tmp/repo"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(onboarding, "HOME_CONFIG_PATH", config_path)

    backend = EngineBackend(id="codex", build_runner=lambda _cfg, _path: None)
    monkeypatch.setattr(onboarding, "list_backends", lambda: [backend])
    monkeypatch.setattr(onboarding.shutil, "which", lambda _cmd: "/usr/bin/codex")

    monkeypatch.setattr(onboarding, "_confirm", _queue_values([True, True, True]))
    monkeypatch.setattr(
        onboarding.questionary, "password", _queue(["123456789:ABCdef"])
    )
    monkeypatch.setattr(onboarding.questionary, "select", _queue(["codex"]))

    def _fake_run(func, *args, **kwargs):
        if func is onboarding._get_bot_info:
            return {"username": "my_bot"}
        if func is onboarding._wait_for_chat:
            return onboarding.ChatInfo(
                chat_id=123,
                username="alice",
                title=None,
                first_name="Alice",
                last_name=None,
                chat_type="private",
            )
        if func is onboarding._send_confirmation:
            return True
        raise AssertionError(f"unexpected anyio.run target: {func}")

    monkeypatch.setattr(onboarding.anyio, "run", _fake_run)

    assert onboarding.interactive_setup(force=True) is True
    saved = config_path.read_text(encoding="utf-8")
    assert "[projects.z80]" in saved
    assert 'path = "/tmp/repo"' in saved
