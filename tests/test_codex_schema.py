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
        "codex_exec_json_phase_and_unknown.jsonl",
    ],
)
def test_codex_schema_parses_fixture(fixture: str) -> None:
    errors = _decode_fixture(fixture)

    assert not errors, f"{fixture} had {len(errors)} errors: " + "; ".join(errors[:5])


def test_codex_schema_decodes_unknown_item_type() -> None:
    event = codex_schema.decode_event(
        '{"type":"item.completed","item":{"id":"item_99","type":"future_item",'
        '"foo":"bar","count":2}}'
    )
    assert isinstance(event, codex_schema.ItemCompleted)
    assert isinstance(event.item, codex_schema.UnknownItem)
    assert event.item.item_type == "future_item"
    assert event.item.payload == {"foo": "bar", "count": 2}
