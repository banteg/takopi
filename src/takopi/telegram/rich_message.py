"""Telegram Bot API rich messages (sendRichMessage) helpers."""

from __future__ import annotations

import re
from typing import Any, Literal

RichMessagesMode = Literal["off", "auto", "always"]

# Documented Rich Message limits (Bot API 10.1).
MAX_RICH_CHARS = 32768
MAX_RICH_BLOCKS = 500
MAX_RICH_COLUMNS = 20

# auto mode only reaches for rich markdown once an answer is substantial.
_MIN_HEADING_ANSWER_CHARS = 400

# GFM table: header row + separator |---|
_GFM_TABLE_SEP_RE = re.compile(r"^\|[\s:\-|]+\|$")
# Level-2+ heading at the start of a line (up to 3 spaces of indent, as in GFM).
_HEADING_RE = re.compile(r"^ {0,3}#{2,6} \S")


def _prose_lines(text: str) -> list[str]:
    """Lines outside fenced code blocks, where markdown structure is real."""
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```"):
            fence = "```"
            continue
        if stripped.startswith("~~~"):
            fence = "~~~"
            continue
        lines.append(line)
    return lines


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|")


def _row_columns(line: str) -> int:
    return len(line.strip().strip("|").split("|"))


def markdown_has_gfm_table(text: str) -> bool:
    lines = _prose_lines(text)
    for header, sep in zip(lines, lines[1:], strict=False):
        if not _is_table_row(header):
            continue
        stripped_sep = sep.strip()
        if "---" in stripped_sep and _GFM_TABLE_SEP_RE.match(stripped_sep):
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
    if len(stripped) <= _MIN_HEADING_ANSWER_CHARS:
        return False
    return any(_HEADING_RE.match(line) for line in _prose_lines(stripped))


def rich_limit_exceeded(markdown: str) -> str | None:
    """Name the documented limit this markdown blows, or None when it fits.

    Blocks are counted the way the Bot API docs describe them (table rows and
    list items each count), so this is an approximation that errs high.
    """
    if len(markdown.encode("utf-8")) > MAX_RICH_CHARS:
        return "chars"
    blocks = 0
    for line in _prose_lines(markdown):
        if _is_table_row(line) and _row_columns(line) > MAX_RICH_COLUMNS:
            return "columns"
        if line.strip():
            blocks += 1
    if blocks > MAX_RICH_BLOCKS:
        return "blocks"
    return None


def escape_raw_html(markdown: str) -> str:
    """Neutralize tag openers outside code, matching what the sulguk path does.

    Rich Markdown accepts arbitrary HTML, and agent answers are full of `Vec<T>`
    and JSX fragments that are not meant as markup. Only `<` is escaped: `>` at
    the start of a line is blockquote syntax.
    """
    out: list[str] = []
    fence: str | None = None
    for line in markdown.splitlines(keepends=True):
        stripped = line.lstrip()
        if fence is not None:
            out.append(line)
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = "```" if stripped.startswith("```") else "~~~"
            out.append(line)
            continue
        # odd segments are inline code spans and are left alone
        segments = line.split("`")
        out.append(
            "`".join(
                seg if index % 2 else seg.replace("<", "&lt;")
                for index, seg in enumerate(segments)
            )
        )
    return "".join(out)


def build_input_rich_message(markdown: str) -> dict[str, Any]:
    """InputRichMessage payload: Rich Markdown (GFM tables, headings, …)."""
    return {"markdown": escape_raw_html(markdown)}
