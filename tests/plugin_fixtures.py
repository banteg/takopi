from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable, Iterable


@dataclass(frozen=True, slots=True)
class FakeDist:
    name: str


class FakeEntryPoint:
    def __init__(
        self,
        name: str,
        value: str,
        group: str,
        *,
        loader: Callable[[], Any] | None = None,
        dist_name: str | None = "takopi",
    ) -> None:
        self.name = name
        self.value = value
        self.group = group
        self._loader = loader or (lambda: None)
        self.dist = FakeDist(dist_name) if dist_name else None

    def load(self) -> Any:
        return self._loader()


class FakeEntryPoints(list):
    def select(self, *, group: str) -> list[FakeEntryPoint]:
        return [ep for ep in self if ep.group == group]

    def get(self, group: str, default: Iterable[Any] | None = None) -> list[Any]:
        _ = default
        return [ep for ep in self if ep.group == group]


def install_entrypoints(monkeypatch, entrypoints: Iterable[FakeEntryPoint]) -> None:
    from takopi import plugins

    def _entry_points() -> FakeEntryPoints:
        return FakeEntryPoints(entrypoints)

    monkeypatch.setattr(plugins, "entry_points", _entry_points)
