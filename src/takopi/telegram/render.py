from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt
from sulguk import transform_html

from ..markdown import MarkdownParts, assemble_markdown_parts

_MD_RENDERER = MarkdownIt("commonmark", {"html": False})
_BULLET_RE = re.compile(r"(?m)^(\s*)•")


def render_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    html = _MD_RENDERER.render(md or "")
    rendered = transform_html(html)

    text = _BULLET_RE.sub(r"\1-", rendered.text)

    entities = [dict(e) for e in rendered.entities]
    return text, entities


def trim_body(body: str | None) -> str | None:
    if not body:
        return None
    if len(body) > 3500:
        body = body[: 3500 - 1] + "…"
    return body if body.strip() else None


def prepare_telegram(parts: MarkdownParts) -> tuple[str, list[dict[str, Any]]]:
    trimmed = MarkdownParts(
        header=parts.header or "",
        body=trim_body(parts.body),
        footer=parts.footer,
    )
    return render_markdown(assemble_markdown_parts(trimmed))
