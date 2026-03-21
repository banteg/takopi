"""Engine capability metadata — transport-neutral, importable by any layer."""

from __future__ import annotations

REASONING_LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh")

_ENGINE_REASONING_LEVELS: dict[str, tuple[str, ...]] = {
    "claude": ("low", "medium", "high", "max"),
    "codex": REASONING_LEVELS,
}

REASONING_SUPPORTED_ENGINES: frozenset[str] = frozenset(_ENGINE_REASONING_LEVELS)


def allowed_reasoning_levels(engine: str) -> tuple[str, ...]:
    """UI-facing helper — returns Codex levels as fallback for unknown engines."""
    return _ENGINE_REASONING_LEVELS.get(engine, REASONING_LEVELS)


def reasoning_levels_for_engine(engine: str) -> tuple[str, ...]:
    """Strict lookup for internal use — raises KeyError for unknown engines."""
    return _ENGINE_REASONING_LEVELS[engine]


def supports_reasoning(engine: str) -> bool:
    return engine in REASONING_SUPPORTED_ENGINES
