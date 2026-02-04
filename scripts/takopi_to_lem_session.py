#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"JSONL not found: {path}")
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _default_out() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Path.home() / ".lem" / "imports" / f"takopi-session-{ts}.txt"


def _render_event(evt: dict) -> str:
    ts = evt.get("ts", "")
    kind = evt.get("kind", "event")
    engine = evt.get("engine") or "-"
    project = evt.get("project") or "-"
    text = (evt.get("text") or "").strip()
    meta = evt.get("meta") or {}
    meta_str = f" meta={meta}" if meta else ""
    return f"[{ts}] {kind} engine={engine} project={project}{meta_str}\n{text}\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert takopi JSONL logs into a LEM session text file."
    )
    parser.add_argument(
        "--jsonl",
        default="~/.takopi/logs/takopi-events.jsonl",
        help="Path to takopi JSONL log file.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for the session text (default: ~/.lem/imports/...).",
    )
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl).expanduser()
    out_path = Path(args.out).expanduser() if args.out else _default_out()

    events = _load_jsonl(jsonl_path)
    events.sort(key=lambda evt: evt.get("ts") or "")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for evt in events:
            handle.write(_render_event(evt))
            handle.write("\n")

    print(f"Wrote session transcript: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
