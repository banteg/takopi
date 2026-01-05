from __future__ import annotations

from typing import Protocol

from .progress import ProgressState
from .transport import RenderedMessage


class Presenter(Protocol):
    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage: ...

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage: ...
