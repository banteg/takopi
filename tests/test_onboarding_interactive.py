from __future__ import annotations

from takopi import onboarding_interactive
from takopi.backends import EngineBackend


def test_mask_token_short() -> None:
    assert onboarding_interactive._mask_token("short") == "*****"


def test_mask_token_long() -> None:
    token = "123456789:ABCdefGH"
    masked = onboarding_interactive._mask_token(token)
    assert masked.startswith("123456789")
    assert masked.endswith("defGH")
    assert "..." in masked


def test_render_config_escapes() -> None:
    config = onboarding_interactive._render_config(
        'token"with\\quote',
        123,
        "codex",
    )
    assert 'default_engine = "codex"' in config
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
    config_path.write_text('bot_token = "token"\nchat_id = 123\n', encoding="utf-8")
    monkeypatch.setattr(onboarding_interactive, "HOME_CONFIG_PATH", config_path)
    assert onboarding_interactive.interactive_setup(force=False) is True


def test_interactive_setup_writes_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr(onboarding_interactive, "HOME_CONFIG_PATH", config_path)

    backend = EngineBackend(id="codex", build_runner=lambda _cfg, _path: None)
    monkeypatch.setattr(onboarding_interactive, "list_backends", lambda: [backend])
    monkeypatch.setattr(
        onboarding_interactive.shutil, "which", lambda _cmd: "/usr/bin/codex"
    )

    monkeypatch.setattr(onboarding_interactive, "_confirm", _queue_values([True, True]))
    monkeypatch.setattr(
        onboarding_interactive.questionary, "password", _queue(["123456789:ABCdef"])
    )
    monkeypatch.setattr(onboarding_interactive.questionary, "select", _queue(["codex"]))
    monkeypatch.setattr(onboarding_interactive.questionary, "text", _queue([""]))

    def _fake_run(func, *args, **kwargs):
        if func is onboarding_interactive._get_bot_info:
            return {"username": "my_bot"}
        if func is onboarding_interactive._wait_for_chat:
            return onboarding_interactive.ChatInfo(
                chat_id=123,
                username="alice",
                title=None,
                first_name="Alice",
                last_name=None,
                chat_type="private",
            )
        if func is onboarding_interactive._send_confirmation:
            return True
        raise AssertionError(f"unexpected anyio.run target: {func}")

    monkeypatch.setattr(onboarding_interactive.anyio, "run", _fake_run)

    assert onboarding_interactive.interactive_setup(force=False) is True
    saved = config_path.read_text(encoding="utf-8")
    assert 'bot_token = "123456789:ABCdef"' in saved
    assert "chat_id = 123" in saved
    assert 'default_engine = "codex"' in saved
