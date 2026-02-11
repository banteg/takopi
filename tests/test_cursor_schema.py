from __future__ import annotations

from pathlib import Path

import pytest

from takopi.schemas import cursor as cursor_schema


def _fixture_path(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / name


def _decode_fixture(name: str) -> list[str]:
    path = _fixture_path(name)
    errors: list[str] = []

    for lineno, line in enumerate(path.read_bytes().splitlines(), 1):
        if not line.strip():
            continue
        try:
            decoded = cursor_schema.decode_event(line)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"line {lineno}: {exc.__class__.__name__}: {exc}")
            continue

        _ = decoded

    return errors


@pytest.mark.parametrize(
    "fixture",
    [
        "cursor_stream_success.jsonl",
        "cursor_stream_error.jsonl",
    ],
)
def test_cursor_schema_parses_fixture(fixture: str) -> None:
    errors = _decode_fixture(fixture)

    assert not errors, f"{fixture} had {len(errors)} errors: " + "; ".join(errors[:5])
