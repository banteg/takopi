from __future__ import annotations

import json
from pathlib import Path

from takopi.event_log import record_event
from takopi.event_log_sqlite import build_sqlite_from_jsonl


def test_build_sqlite_from_jsonl(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "events.jsonl"
    sqlite_path = tmp_path / "events.db"

    record_event(
        path=jsonl_path,
        kind="prompt",
        chat_id=123,
        thread_id=None,
        message_id=1,
        engine="codex",
        project="kite-trading-platform",
        text="hello",
        meta={"voice": False},
        max_text_chars=20000,
    )
    record_event(
        path=jsonl_path,
        kind="final",
        chat_id=123,
        thread_id=None,
        message_id=2,
        engine="codex",
        project="kite-trading-platform",
        text="done",
        meta={"ok": True},
        max_text_chars=20000,
    )

    rows = build_sqlite_from_jsonl(jsonl_path, sqlite_path)
    assert rows == 2

    import sqlite3

    conn = sqlite3.connect(sqlite_path)
    try:
        cur = conn.execute("SELECT kind, meta_json FROM takopi_events ORDER BY ts")
        results = cur.fetchall()
    finally:
        conn.close()

    assert results[0][0] == "prompt"
    assert json.loads(results[1][1])["ok"] is True
