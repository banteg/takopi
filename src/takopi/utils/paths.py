from __future__ import annotations

from contextvars import ContextVar, Token
from pathlib import Path


_run_base_dir: ContextVar[Path | None] = ContextVar("takopi_run_base_dir", default=None)


def get_run_base_dir() -> Path | None:
    return _run_base_dir.get()


def set_run_base_dir(base_dir: Path | None) -> Token[Path | None]:
    return _run_base_dir.set(base_dir)


def reset_run_base_dir(token: Token[Path | None]) -> None:
    _run_base_dir.reset(token)


def _path_variants(base: str) -> tuple[str, ...]:
    normalized = base.rstrip("/\\")
    if not normalized:
        return ()
    variants: list[str] = []
    for candidate in (
        normalized,
        normalized.replace("\\", "/"),
        normalized.replace("/", "\\"),
    ):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return tuple(variants)


def relativize_path(value: str, *, base_dir: Path | None = None) -> str:
    if not value:
        return value
    base = get_run_base_dir() if base_dir is None else base_dir
    if base is None:
        base = Path.cwd()
    base_str = str(base)
    if not base_str:
        return value
    for base_variant in _path_variants(base_str):
        if value == base_variant:
            return "."
        for sep in ("/", "\\"):
            prefix = f"{base_variant}{sep}"
            if value.startswith(prefix):
                suffix = value[len(prefix) :]
                return (suffix or ".").replace("\\", "/")
    return value


def relativize_command(value: str, *, base_dir: Path | None = None) -> str:
    base = get_run_base_dir() if base_dir is None else base_dir
    if base is None:
        base = Path.cwd()
    base_str = str(base)
    out = value
    for base_variant in _path_variants(base_str):
        for sep in ("/", "\\"):
            out = out.replace(f"{base_variant}{sep}", "")
    return out
