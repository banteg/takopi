from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt
from sulguk import transform_html

from ..markdown import MarkdownParts, assemble_markdown_parts

_MD_RENDERER = MarkdownIt("commonmark", {"html": False})
_BULLET_RE = re.compile(r"(?m)^(\s*)•")
_LIST_ITEM_RE = re.compile(r"^(\s*)(?:\d+[.)]|[-+*])\s+\S")
_LIST_MARKER_ONLY_RE = re.compile(r"^(\s*)(\d+[.)]|[-+*])\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _tighten_list_whitespace(md: str) -> str:
    lines = md.splitlines()
    if not lines:
        return md
    out: list[str] = []
    in_fence = False
    fence = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence = marker
            elif marker == fence:
                in_fence = False
                fence = ""
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        marker_match = _LIST_MARKER_ONLY_RE.match(line)
        if marker_match:
            indent, marker = marker_match.groups()
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                next_line = lines[j]
                if _LIST_MARKER_ONLY_RE.match(next_line) or _LIST_ITEM_RE.match(
                    next_line
                ):
                    out.append(line)
                    i += 1
                    continue
                out.append(f"{indent}{marker} {next_line.lstrip()}")
                i = j + 1
                continue

        if not line.strip():
            if out and _LIST_ITEM_RE.match(out[-1]):
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j < len(lines) and (
                    _LIST_ITEM_RE.match(lines[j])
                    or _LIST_MARKER_ONLY_RE.match(lines[j])
                ):
                    i += 1
                    continue

        out.append(line)
        i += 1

    return "\n".join(out)


def render_markdown(md: str) -> tuple[str, list[dict[str, Any]]]:
    md = _tighten_list_whitespace(md or "")
    html = _MD_RENDERER.render(md)
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
