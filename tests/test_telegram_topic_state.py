import pytest

from takopi.context import RunContext
from takopi.model import ResumeToken
from takopi.telegram.topic_state import TopicStateStore


@pytest.mark.anyio
async def test_topic_state_store_roundtrip(tmp_path) -> None:
    path = tmp_path / "telegram_topics_state.json"
    store = TopicStateStore(path)
    context = RunContext(project="proj", branch="feat/topic")
    await store.set_context(1, 10, context)
    await store.set_default_engine(1, 10, "claude")
    await store.set_session_resume(1, 10, ResumeToken(engine="codex", value="abc123"))

    snapshot = await store.get_thread(1, 10)
    assert snapshot is not None
    assert snapshot.context == context
    assert snapshot.sessions == {"codex": "abc123"}
    assert snapshot.default_engine == "claude"

    store2 = TopicStateStore(path)
    snapshot2 = await store2.get_thread(1, 10)
    assert snapshot2 is not None
    assert snapshot2.context == context
    assert snapshot2.sessions == {"codex": "abc123"}
    assert snapshot2.default_engine == "claude"


@pytest.mark.anyio
async def test_topic_state_store_clear_and_find(tmp_path) -> None:
    path = tmp_path / "telegram_topics_state.json"
    store = TopicStateStore(path)
    context = RunContext(project="proj", branch="main")
    await store.set_context(2, 20, context)
    await store.set_session_resume(
        2, 20, ResumeToken(engine="claude", value="resume-token")
    )

    found = await store.find_thread_for_context(2, context)
    assert found == 20

    await store.clear_sessions(2, 20)
    snapshot = await store.get_thread(2, 20)
    assert snapshot is not None
    assert snapshot.sessions == {}

    await store.clear_context(2, 20)
    snapshot = await store.get_thread(2, 20)
    assert snapshot is not None
    assert snapshot.context is None
    await store.clear_default_engine(2, 20)
    snapshot = await store.get_thread(2, 20)
    assert snapshot is not None
    assert snapshot.default_engine is None


@pytest.mark.anyio
async def test_topic_state_store_delete_thread(tmp_path) -> None:
    path = tmp_path / "telegram_topics_state.json"
    store = TopicStateStore(path)
    context = RunContext(project="proj", branch="main")
    await store.set_context(1, 10, context)
    await store.set_session_resume(1, 10, ResumeToken(engine="codex", value="abc123"))

    await store.delete_thread(1, 10)

    assert await store.get_thread(1, 10) is None
    assert await store.find_thread_for_context(1, context) is None
