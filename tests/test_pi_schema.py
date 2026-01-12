from __future__ import annotations

from pathlib import Path

import pytest

from takopi.schemas import pi as pi_schema


def _fixture_path(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / name


def _decode_fixture(name: str) -> list[str]:
    path = _fixture_path(name)
    errors: list[str] = []

    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            pi_schema.decode_event(line)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"line {lineno}: {exc.__class__.__name__}: {exc}")

    return errors


@pytest.mark.parametrize(
    "fixture",
    [
        "pi_stream_success.jsonl",
        "pi_stream_error.jsonl",
        "pi_print_mode_events.jsonl",
    ],
)
def test_pi_schema_parses_fixture(fixture: str) -> None:
    errors = _decode_fixture(fixture)
    assert not errors, f"{fixture} had {len(errors)} errors: " + "; ".join(errors[:5])
