from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt
from sulguk import transform_html

_md = MarkdownIt("commonmark", {"html": False})


def render_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    html = _md.render(md or "")
    rendered = transform_html(html)

    text = re.sub(r"(?m)^(\s*)•", r"\1-", rendered.text)

    # FIX: Telegram requires MessageEntity.language (if present) to be a String.
    entities: list[dict[str, Any]] = []
    for e in rendered.entities:
        d = dict(e)
        if "language" in d and not isinstance(d["language"], str):
            d.pop("language", None)
        entities.append(d)
    return text, entities

