import pytest

from takopi.telegram.chat_prefs import ChatPrefsStore


@pytest.mark.anyio
async def test_chat_prefs_store_roundtrip(tmp_path) -> None:
    path = tmp_path / "telegram_chat_prefs_state.json"
    store = ChatPrefsStore(path)
    await store.set_default_engine(123, "codex")
    await store.set_trigger_mode(123, "mentions")
    await store.set_default_engine(123, "codex")
    await store.clear_default_engine(456)

    assert await store.get_default_engine(123) == "codex"
    assert await store.get_trigger_mode(123) == "mentions"

    store2 = ChatPrefsStore(path)
    assert await store2.get_default_engine(123) == "codex"
    assert await store2.get_trigger_mode(123) == "mentions"

    await store2.clear_default_engine(123)
    assert await store2.get_default_engine(123) is None
    assert await store2.get_trigger_mode(123) == "mentions"

    await store2.clear_trigger_mode(123)
    assert await store2.get_trigger_mode(123) is None
