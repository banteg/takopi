from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .event_log import read_events_jsonl


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS takopi_events")
    conn.execute(
        """
        CREATE TABLE takopi_events (
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            thread_id INTEGER,
            message_id INTEGER,
            engine TEXT,
            project TEXT,
            text TEXT,
            meta_json TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_takopi_events_kind_ts "
        "ON takopi_events(kind, ts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_takopi_events_project_ts "
        "ON takopi_events(project, ts)"
    )


def build_sqlite_from_jsonl(jsonl_path: Path, sqlite_path: Path) -> int:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    events = read_events_jsonl(jsonl_path)
    conn = sqlite3.connect(sqlite_path)
    try:
        _init_schema(conn)
        rows = [
            (
                event.get("ts"),
                event.get("kind"),
                event.get("chat_id"),
                event.get("thread_id"),
                event.get("message_id"),
                event.get("engine"),
                event.get("project"),
                event.get("text"),
                json.dumps(event.get("meta") or {}),
            )
            for event in events
        ]
        conn.executemany(
            """
            INSERT INTO takopi_events (
                ts, kind, chat_id, thread_id, message_id, engine,
                project, text, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
