from __future__ import annotations

import json
from pathlib import Path

import pytest

from takopi.schemas import codex as codex_schema


def _fixture_path(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / name


def _decode_fixture(name: str) -> list[str]:
    path = _fixture_path(name)
    errors: list[str] = []

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"line {lineno}: invalid JSON ({exc})")
            continue
        try:
            codex_schema.decode_event(line)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"line {lineno}: {exc.__class__.__name__}: {exc}")

    return errors


@pytest.mark.parametrize(
    "fixture",
    [
        "codex_exec_json_all_formats.jsonl",
    ],
)
def test_codex_schema_parses_fixture(fixture: str) -> None:
    errors = _decode_fixture(fixture)

    assert not errors, f"{fixture} had {len(errors)} errors: " + "; ".join(errors[:5])
