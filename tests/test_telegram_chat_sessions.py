from pathlib import Path

import pytest

from takopi.model import ResumeToken
from takopi.telegram.chat_sessions import ChatSessionStore


@pytest.mark.anyio
async def test_chat_sessions_store_roundtrip(tmp_path) -> None:
    path = tmp_path / "telegram_chat_sessions_state.json"
    store = ChatSessionStore(path)
    await store.set_session_resume(1, None, ResumeToken(engine="codex", value="abc123"))
    await store.set_session_resume(1, 42, ResumeToken(engine="claude", value="res-1"))

    stored_private = await store.get_session_resume(1, None, "codex")
    stored_group = await store.get_session_resume(1, 42, "claude")
    assert stored_private == ResumeToken(engine="codex", value="abc123")
    assert stored_group == ResumeToken(engine="claude", value="res-1")

    store2 = ChatSessionStore(path)
    stored_private_2 = await store2.get_session_resume(1, None, "codex")
    stored_group_2 = await store2.get_session_resume(1, 42, "claude")
    assert stored_private_2 == ResumeToken(engine="codex", value="abc123")
    assert stored_group_2 == ResumeToken(engine="claude", value="res-1")


@pytest.mark.anyio
async def test_chat_sessions_store_clear(tmp_path) -> None:
    path = tmp_path / "telegram_chat_sessions_state.json"
    store = ChatSessionStore(path)
    await store.set_session_resume(2, None, ResumeToken(engine="codex", value="one"))
    await store.set_session_resume(2, 77, ResumeToken(engine="codex", value="two"))

    await store.clear_sessions(2, None)
    assert await store.get_session_resume(2, None, "codex") is None
    assert await store.get_session_resume(2, 77, "codex") == ResumeToken(
        engine="codex",
        value="two",
    )


@pytest.mark.anyio
async def test_chat_sessions_store_drops_sessions_on_cwd_change(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "telegram_chat_sessions_state.json"
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()

    monkeypatch.chdir(dir1)
    store = ChatSessionStore(path)
    await store.set_session_resume(1, None, ResumeToken(engine="codex", value="abc123"))
    assert await store.get_session_resume(1, None, "codex") == ResumeToken(
        engine="codex", value="abc123"
    )

    store2 = ChatSessionStore(path)
    assert await store2.sync_startup_cwd(Path.cwd()) is False
    assert await store2.get_session_resume(1, None, "codex") == ResumeToken(
        engine="codex", value="abc123"
    )

    monkeypatch.chdir(dir2)
    store3 = ChatSessionStore(path)
    assert await store3.sync_startup_cwd(Path.cwd()) is True
    assert await store3.get_session_resume(1, None, "codex") is None
