from __future__ import annotations

import json
from pathlib import Path

from takopi.event_log import record_event


def _read_jsonl(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").strip())


def test_record_event_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"

    record_event(
        path=path,
        kind="prompt",
        chat_id=123,
        thread_id=456,
        message_id=789,
        engine="codex",
        project="kite-trading-platform",
        text="hello world",
        meta={"voice": False},
        max_text_chars=20000,
    )

    payload = _read_jsonl(path)
    assert payload["kind"] == "prompt"
    assert payload["chat_id"] == 123
    assert payload["thread_id"] == 456
    assert payload["message_id"] == 789
    assert payload["engine"] == "codex"
    assert payload["project"] == "kite-trading-platform"
    assert payload["text"] == "hello world"
    assert payload["meta"]["voice"] is False


def test_record_event_truncates_and_redacts(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    secret = "8268029247:AAFhepiJwV9Z7C8KYAbBYrqU4_9tbFTomBE"

    record_event(
        path=path,
        kind="final",
        chat_id=1,
        thread_id=None,
        message_id=None,
        engine="codex",
        project=None,
        text=secret + " " + ("x" * 20),
        meta={},
        max_text_chars=10,
    )

    payload = _read_jsonl(path)
    assert payload["text"].endswith("…")
    assert "REDACTED" in payload["text"]
