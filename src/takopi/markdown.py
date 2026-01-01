"""Markdown rendering and truncation helpers for Telegram constraints."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from markdown_it import MarkdownIt
from sulguk import transform_html

TELEGRAM_MARKDOWN_LIMIT = 3500

_md = MarkdownIt("commonmark", {"html": False})


def render_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    html = _md.render(md or "")
    rendered = transform_html(html)

    text = re.sub(r"(?m)^(\s*)•", r"\1-", rendered.text)

    entities = [dict(e) for e in rendered.entities]
    return text, entities


def truncate_for_telegram(
    text: str, limit: int, *, is_resume_line: Callable[[str], bool]
) -> str:
    """
    Truncate text to fit Telegram limits while preserving the trailing resume command
    line (if present), otherwise preserving the last non-empty line.
    """
    if len(text) <= limit:
        return text

    lines = text.splitlines()

    tail_lines: list[str] | None = None
    is_resume_tail = False
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if is_resume_line(line):
            tail_lines = lines[i:]
            is_resume_tail = True
            break

    if tail_lines is None:
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                tail_lines = [lines[i]]
                break

    tail = "\n".join(tail_lines or []).strip("\n")
    sep = "\n…\n"

    max_tail = limit if is_resume_tail else (limit // 4)
    tail = tail[-max_tail:] if max_tail > 0 else ""

    head_budget = limit - len(sep) - len(tail)
    if head_budget <= 0:
        return tail[-limit:] if tail else text[:limit]

    head = text[:head_budget].rstrip()
    return (head + sep + tail)[:limit]


def prepare_telegram(
    md: str,
    *,
    limit: int,
    is_resume_line: Callable[[str], bool] | None = None,
) -> tuple[str, list[dict[str, Any]] | None]:
    rendered, entities = render_markdown(md)
    if len(rendered) > limit:
        if is_resume_line is None:

            def _never_resume_line(_line: str) -> bool:
                return False

            is_resume_line = _never_resume_line
        rendered = truncate_for_telegram(rendered, limit, is_resume_line=is_resume_line)
        return rendered, None
    return rendered, entities
