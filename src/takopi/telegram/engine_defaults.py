from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..context import RunContext
from ..model import EngineId
from ..transport_runtime import TransportRuntime
from .chat_prefs import ChatPrefsStore
from .topic_state import TopicStateStore

EngineSource = Literal[
    "directive",
    "topic_default",
    "chat_default",
    "project_default",
    "global_default",
]


@dataclass(frozen=True, slots=True)
class EngineResolution:
    engine: EngineId
    source: EngineSource
    topic_default: EngineId | None
    chat_default: EngineId | None
    project_default: EngineId | None


async def resolve_engine_for_message(
    *,
    runtime: TransportRuntime,
    context: RunContext | None,
    explicit_engine: EngineId | None,
    chat_id: int,
    topic_key: tuple[int, int] | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
) -> EngineResolution:
    topic_default = None
    if topic_store is not None and topic_key is not None:
        topic_default = await topic_store.get_default_engine(*topic_key)
    chat_default = None
    if chat_prefs is not None:
        chat_default = await chat_prefs.get_default_engine(chat_id)
    project_default = runtime.project_default_engine(context)

    if explicit_engine is not None:
        return EngineResolution(
            engine=explicit_engine,
            source="directive",
            topic_default=topic_default,
            chat_default=chat_default,
            project_default=project_default,
        )
    if topic_default is not None:
        return EngineResolution(
            engine=topic_default,
            source="topic_default",
            topic_default=topic_default,
            chat_default=chat_default,
            project_default=project_default,
        )
    if chat_default is not None:
        return EngineResolution(
            engine=chat_default,
            source="chat_default",
            topic_default=topic_default,
            chat_default=chat_default,
            project_default=project_default,
        )
    if project_default is not None:
        return EngineResolution(
            engine=project_default,
            source="project_default",
            topic_default=topic_default,
            chat_default=chat_default,
            project_default=project_default,
        )
    return EngineResolution(
        engine=runtime.default_engine,
        source="global_default",
        topic_default=topic_default,
        chat_default=chat_default,
        project_default=project_default,
    )
