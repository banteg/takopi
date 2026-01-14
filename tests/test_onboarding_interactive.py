from __future__ import annotations

import anyio
from functools import partial

from takopi.backends import EngineBackend
from takopi.config import dump_toml
from takopi.telegram import onboarding
from takopi.telegram.api_models import User


def test_mask_token_short() -> None:
    assert onboarding.mask_token("short") == "*****"


def test_mask_token_long() -> None:
    token = "123456789:ABCdefGH"
    masked = onboarding.mask_token(token)
    assert masked.startswith("123456789")
    assert masked.endswith("defGH")
    assert "..." in masked


def test_render_config_escapes() -> None:
    config = dump_toml(
        {
            "default_engine": "codex",
            "transport": "telegram",
            "transports": {
                "telegram": {
                    "bot_token": 'token"with\\quote',
                    "chat_id": 123,
                }
            },
        }
    )
    assert 'default_engine = "codex"' in config
    assert 'transport = "telegram"' in config
    assert "[transports.telegram]" in config
    assert 'bot_token = "token\\"with\\\\quote"' in config
    assert "chat_id = 123" in config
    assert config.endswith("\n")


class FakeQuestion:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value

    async def ask_async(self):
        return self._value


def queue_answers(values):
    it = iter(values)

    def _make(*_args, **_kwargs):
        return FakeQuestion(next(it))

    return _make


def queue_values(values):
    it = iter(values)

    async def _next(*_args, **_kwargs):
        return next(it)

    return _next


def patch_live_services(
    monkeypatch,
    *,
    bot: User,
    chat: onboarding.ChatInfo,
    topics_issue=None,
) -> None:
    async def _get_bot_info(self, _token: str):
        return bot

    async def _wait_for_chat(self, _token: str):
        return chat

    async def _validate_topics(self, _token: str, _chat_id: int, _scope):
        return topics_issue

    monkeypatch.setattr(onboarding.LiveServices, "get_bot_info", _get_bot_info)
    monkeypatch.setattr(onboarding.LiveServices, "wait_for_chat", _wait_for_chat)
    monkeypatch.setattr(onboarding.LiveServices, "validate_topics", _validate_topics)


def test_interactive_setup_skips_when_config_exists(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    config_path.write_text(
        'transport = "telegram"\n\n[transports.telegram]\n'
        'bot_token = "token"\nchat_id = 123\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(onboarding, "HOME_CONFIG_PATH", config_path)
    assert anyio.run(partial(onboarding.interactive_setup, force=False)) is True


def test_interactive_setup_writes_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr(onboarding, "HOME_CONFIG_PATH", config_path)

    backend = EngineBackend(id="codex", build_runner=lambda _cfg, _path: None)
    monkeypatch.setattr(onboarding, "list_backends", lambda: [backend])
    monkeypatch.setattr(onboarding.shutil, "which", lambda _cmd: "/usr/bin/codex")

    monkeypatch.setattr(onboarding, "confirm_prompt", queue_values([True, True]))
    monkeypatch.setattr(
        onboarding.questionary, "password", queue_answers(["123456789:ABCdef"])
    )
    monkeypatch.setattr(
        onboarding.questionary,
        "select",
        queue_answers(["assistant", "codex"]),
    )
    patch_live_services(
        monkeypatch,
        bot=User(id=1, username="my_bot"),
        chat=onboarding.ChatInfo(
            chat_id=123,
            username="alice",
            title=None,
            first_name="Alice",
            last_name=None,
            chat_type="private",
        ),
    )

    assert anyio.run(partial(onboarding.interactive_setup, force=False)) is True
    saved = config_path.read_text(encoding="utf-8")
    assert 'transport = "telegram"' in saved
    assert "[transports.telegram]" in saved
    assert 'bot_token = "123456789:ABCdef"' in saved
    assert "chat_id = 123" in saved
    assert 'session_mode = "chat"' in saved
    assert "show_resume_line = false" in saved
    assert "[transports.telegram.topics]" in saved
    assert "enabled = false" in saved
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

    monkeypatch.setattr(onboarding, "confirm_prompt", queue_values([True, True, True]))
    monkeypatch.setattr(
        onboarding.questionary, "password", queue_answers(["123456789:ABCdef"])
    )
    monkeypatch.setattr(
        onboarding.questionary,
        "select",
        queue_answers(["assistant", "codex"]),
    )
    patch_live_services(
        monkeypatch,
        bot=User(id=1, username="my_bot"),
        chat=onboarding.ChatInfo(
            chat_id=123,
            username="alice",
            title=None,
            first_name="Alice",
            last_name=None,
            chat_type="private",
        ),
    )

    assert anyio.run(partial(onboarding.interactive_setup, force=True)) is True
    saved = config_path.read_text(encoding="utf-8")
    assert "[projects.z80]" in saved
    assert 'path = "/tmp/repo"' in saved


def test_interactive_setup_no_agents_aborts(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    monkeypatch.setattr(onboarding, "HOME_CONFIG_PATH", config_path)

    backend = EngineBackend(id="codex", build_runner=lambda _cfg, _path: None)
    monkeypatch.setattr(onboarding, "list_backends", lambda: [backend])
    monkeypatch.setattr(onboarding.shutil, "which", lambda _cmd: None)

    monkeypatch.setattr(onboarding, "confirm_prompt", queue_values([True, False]))
    monkeypatch.setattr(
        onboarding.questionary, "password", queue_answers(["123456789:ABCdef"])
    )
    monkeypatch.setattr(
        onboarding.questionary,
        "select",
        queue_answers(["assistant"]),
    )
    patch_live_services(
        monkeypatch,
        bot=User(id=1, username="my_bot"),
        chat=onboarding.ChatInfo(
            chat_id=123,
            username="alice",
            title=None,
            first_name="Alice",
            last_name=None,
            chat_type="private",
        ),
    )

    assert anyio.run(partial(onboarding.interactive_setup, force=False)) is False
    assert not config_path.exists()


def test_interactive_setup_recovers_from_malformed_toml(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "takopi.toml"
    bad_toml = 'transport = "telegram"\n[transports\n'
    config_path.write_text(bad_toml, encoding="utf-8")
    monkeypatch.setattr(onboarding, "HOME_CONFIG_PATH", config_path)

    backend = EngineBackend(id="codex", build_runner=lambda _cfg, _path: None)
    monkeypatch.setattr(onboarding, "list_backends", lambda: [backend])
    monkeypatch.setattr(onboarding.shutil, "which", lambda _cmd: "/usr/bin/codex")

    monkeypatch.setattr(onboarding, "confirm_prompt", queue_values([True, True, True]))
    monkeypatch.setattr(
        onboarding.questionary, "password", queue_answers(["123456789:ABCdef"])
    )
    monkeypatch.setattr(
        onboarding.questionary,
        "select",
        queue_answers(["assistant", "codex"]),
    )
    patch_live_services(
        monkeypatch,
        bot=User(id=1, username="my_bot"),
        chat=onboarding.ChatInfo(
            chat_id=123,
            username="alice",
            title=None,
            first_name="Alice",
            last_name=None,
            chat_type="private",
        ),
    )

    assert anyio.run(partial(onboarding.interactive_setup, force=True)) is True
    backup = config_path.with_suffix(".toml.bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == bad_toml
    saved = config_path.read_text(encoding="utf-8")
    assert "[transports.telegram]" in saved
    assert 'bot_token = "123456789:ABCdef"' in saved


def test_capture_chat_id_with_token(monkeypatch) -> None:
    patch_live_services(
        monkeypatch,
        bot=User(id=1, username="my_bot"),
        chat=onboarding.ChatInfo(
            chat_id=456,
            username=None,
            title="takopi",
            first_name=None,
            last_name=None,
            chat_type="supergroup",
        ),
    )

    chat = anyio.run(partial(onboarding.capture_chat_id, token="123456789:ABCdef"))

    assert chat is not None
    assert chat.chat_id == 456


def test_capture_chat_id_prompts_for_token(monkeypatch) -> None:
    async def _prompt_token(_ui, _svc):
        return ("token", User(id=1, username="bot"))

    monkeypatch.setattr(onboarding, "prompt_token", _prompt_token)
    patch_live_services(
        monkeypatch,
        bot=User(id=1, username="bot"),
        chat=onboarding.ChatInfo(
            chat_id=789,
            username="alice",
            title=None,
            first_name="Alice",
            last_name=None,
            chat_type="private",
        ),
    )

    chat = anyio.run(onboarding.capture_chat_id)

    assert chat is not None
    assert chat.chat_id == 789
