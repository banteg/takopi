from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import msgspec

OverrideSource = Literal["topic_override", "chat_default", "default"]

REASONING_LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh")
REASONING_SUPPORTED_ENGINES = frozenset({"codex"})


class EngineOverrides(msgspec.Struct, forbid_unknown_fields=False):
    model: str | None = None
    reasoning: str | None = None


@dataclass(frozen=True, slots=True)
class OverrideValueResolution:
    value: str | None
    source: OverrideSource
    topic_value: str | None
    chat_value: str | None


def normalize_override_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_overrides(overrides: EngineOverrides | None) -> EngineOverrides | None:
    if overrides is None:
        return None
    model = normalize_override_value(overrides.model)
    reasoning = normalize_override_value(overrides.reasoning)
    if model is None and reasoning is None:
        return None
    return EngineOverrides(model=model, reasoning=reasoning)


def merge_overrides(
    topic_override: EngineOverrides | None,
    chat_override: EngineOverrides | None,
) -> EngineOverrides | None:
    topic = normalize_overrides(topic_override)
    chat = normalize_overrides(chat_override)
    if topic is None and chat is None:
        return None
    model = None
    reasoning = None
    if topic is not None and topic.model is not None:
        model = topic.model
    elif chat is not None:
        model = chat.model
    if topic is not None and topic.reasoning is not None:
        reasoning = topic.reasoning
    elif chat is not None:
        reasoning = chat.reasoning
    return normalize_overrides(EngineOverrides(model=model, reasoning=reasoning))


def resolve_override_value(
    *,
    topic_override: EngineOverrides | None,
    chat_override: EngineOverrides | None,
    field: Literal["model", "reasoning"],
) -> OverrideValueResolution:
    topic_value = normalize_override_value(
        getattr(topic_override, field, None) if topic_override is not None else None
    )
    chat_value = normalize_override_value(
        getattr(chat_override, field, None) if chat_override is not None else None
    )
    if topic_value is not None:
        return OverrideValueResolution(
            value=topic_value,
            source="topic_override",
            topic_value=topic_value,
            chat_value=chat_value,
        )
    if chat_value is not None:
        return OverrideValueResolution(
            value=chat_value,
            source="chat_default",
            topic_value=topic_value,
            chat_value=chat_value,
        )
    return OverrideValueResolution(
        value=None,
        source="default",
        topic_value=topic_value,
        chat_value=chat_value,
    )


def allowed_reasoning_levels(engine: str) -> tuple[str, ...]:
    _ = engine
    return REASONING_LEVELS


def supports_reasoning(engine: str) -> bool:
    return engine in REASONING_SUPPORTED_ENGINES
