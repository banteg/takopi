from dataclasses import replace

from takopi.settings import TelegramTopicsSettings
from takopi.telegram.topics import _resolve_topics_scope_raw, _topics_command_error
from tests.telegram_fakes import FakeTransport, make_cfg


def test_resolve_topics_scope_raw() -> None:
    resolved, chat_ids = _resolve_topics_scope_raw("auto", 1, ())
    assert resolved == "main"
    assert chat_ids == frozenset({1})

    resolved, chat_ids = _resolve_topics_scope_raw("projects", 1, (2, 3))
    assert resolved == "projects"
    assert chat_ids == frozenset({2, 3})

    resolved, chat_ids = _resolve_topics_scope_raw("all", 1, (2,))
    assert resolved == "all"
    assert chat_ids == frozenset({1, 2})


def test_topics_command_error_for_wrong_chat() -> None:
    transport = FakeTransport()
    cfg = replace(
        make_cfg(transport),
        topics=TelegramTopicsSettings(enabled=True, scope="main"),
    )
    error = _topics_command_error(
        cfg,
        chat_id=999,
        resolved_scope="main",
        scope_chat_ids=frozenset({cfg.chat_id}),
    )
    assert error == "topics commands are only available in the main chat."
