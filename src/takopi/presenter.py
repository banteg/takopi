from __future__ import annotations

from typing import Protocol

from .render import MarkdownParts
from .transport import RenderedMessage


class Presenter(Protocol):
    def render(self, parts: MarkdownParts) -> RenderedMessage: ...
