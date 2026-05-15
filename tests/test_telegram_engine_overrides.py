import pytest

from takopi.telegram.chat_prefs import ChatPrefsStore
from takopi.telegram.engine_overrides import (
    EngineOverrides,
    merge_overrides,
    resolve_override_value,
)
from takopi.telegram.topic_state import TopicStateStore


def test_merge_overrides_prefers_topic_values() -> None:
    topic = EngineOverrides(model=None, reasoning="high", mode="plan")
    chat = EngineOverrides(model="gpt-4.1-mini", reasoning=None, mode="build")
    merged = merge_overrides(topic, chat)

    assert merged is not None
    assert merged.model == "gpt-4.1-mini"
    assert merged.reasoning == "high"
    assert merged.mode == "plan"


def test_resolve_override_value_tracks_sources() -> None:
    topic = EngineOverrides(model="gpt-4.1", reasoning=None, mode="plan")
    chat = EngineOverrides(model="gpt-4.1-mini", reasoning="low", mode="build")
    resolution = resolve_override_value(
        topic_override=topic,
        chat_override=chat,
        field="model",
    )

    assert resolution.value == "gpt-4.1"
    assert resolution.source == "topic_override"
    assert resolution.topic_value == "gpt-4.1"
    assert resolution.chat_value == "gpt-4.1-mini"

    mode_resolution = resolve_override_value(
        topic_override=topic,
        chat_override=chat,
        field="mode",
    )
    assert mode_resolution.value == "plan"
    assert mode_resolution.source == "topic_override"


@pytest.mark.anyio
async def test_chat_prefs_engine_overrides_roundtrip(tmp_path) -> None:
    path = tmp_path / "telegram_chat_prefs_state.json"
    store = ChatPrefsStore(path)
    await store.set_engine_override(
        123,
        "codex",
        EngineOverrides(model="gpt-4.1-mini", reasoning="low", mode="build"),
    )

    override = await store.get_engine_override(123, "codex")
    assert override is not None
    assert override.model == "gpt-4.1-mini"
    assert override.reasoning == "low"
    assert override.mode == "build"

    store2 = ChatPrefsStore(path)
    override2 = await store2.get_engine_override(123, "codex")
    assert override2 is not None
    assert override2.model == "gpt-4.1-mini"
    assert override2.reasoning == "low"
    assert override2.mode == "build"

    await store2.set_engine_override(
        123,
        "codex",
        EngineOverrides(model=None, reasoning="low", mode="build"),
    )
    override3 = await store2.get_engine_override(123, "codex")
    assert override3 is not None
    assert override3.model is None
    assert override3.reasoning == "low"
    assert override3.mode == "build"

    await store2.set_engine_override(
        123,
        "codex",
        EngineOverrides(model=None, reasoning=None, mode=None),
    )
    override4 = await store2.get_engine_override(123, "codex")
    assert override4 is None


@pytest.mark.anyio
async def test_topic_state_engine_overrides_roundtrip(tmp_path) -> None:
    path = tmp_path / "telegram_topics_state.json"
    store = TopicStateStore(path)
    await store.set_engine_override(
        1,
        10,
        "codex",
        EngineOverrides(model="gpt-4.1", reasoning="medium", mode="plan"),
    )

    override = await store.get_engine_override(1, 10, "codex")
    assert override is not None
    assert override.model == "gpt-4.1"
    assert override.reasoning == "medium"
    assert override.mode == "plan"

    store2 = TopicStateStore(path)
    override2 = await store2.get_engine_override(1, 10, "codex")
    assert override2 is not None
    assert override2.model == "gpt-4.1"
    assert override2.reasoning == "medium"
    assert override2.mode == "plan"
