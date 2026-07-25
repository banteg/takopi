"""Telegram Bot API rich messages (sendRichMessage) helpers."""

from __future__ import annotations

import re
from typing import Any, Literal

RichMessagesMode = Literal["off", "auto", "always"]

# GFM table: header row + separator |---|
_GFM_TABLE_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")


def markdown_has_gfm_table(text: str) -> bool:
    lines = text.splitlines()
    for i in range(len(lines) - 1):
        header = lines[i].strip()
        sep = lines[i + 1].strip()
        if not (header.startswith("|") and header.endswith("|")):
            continue
        if "---" in sep and _GFM_TABLE_SEP_RE.match(sep):
            return True
    return False


def should_use_rich_message(text: str, mode: RichMessagesMode) -> bool:
    if mode == "off":
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if mode == "always":
        return True
    # auto: tables (common agent reports) or level-2+ headings in long answers
    if markdown_has_gfm_table(stripped):
        return True
    if "## " in stripped and len(stripped) > 400:
        return True
    return False


def build_input_rich_message(markdown: str) -> dict[str, Any]:
    """InputRichMessage payload: Rich Markdown (GFM tables, headings, …)."""
    return {
        "markdown": markdown,
        "skip_entity_detection": False,
    }
