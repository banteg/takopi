from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from takopi.model import Action, ActionEvent, ResumeToken, StartedEvent, TakopiEvent

from takopi.render import (
    ExecProgressRenderer,
    MarkdownParts,
    STATUS,
    action_status,
    assemble_markdown_parts,
    format_elapsed,
    format_file_change_title,
    prepare_telegram_split,
    render_event_cli,
    render_markdown,
    shorten,
    split_body,
)
from tests.factories import (
    action_completed,
    action_started,
    session_started,
)


def _format_resume(token) -> str:
    return f"`codex resume {token.value}`"


SAMPLE_EVENTS: list[TakopiEvent] = [
    session_started("codex", "0199a213-81c0-7800-8aa1-bbab2a035a53", title="Codex"),
    action_started("a-1", "command", "bash -lc ls"),
    action_completed(
        "a-1",
        "command",
        "bash -lc ls",
        ok=True,
        detail={"exit_code": 0},
    ),
    action_completed("a-2", "note", "Checking repository root for README", ok=True),
]


def test_render_event_cli_sample_events() -> None:
    out: list[str] = []
    for evt in SAMPLE_EVENTS:
        out.extend(render_event_cli(evt))

    assert out == [
        "codex",
        "▸ `bash -lc ls`",
        "✓ `bash -lc ls`",
        "✓ Checking repository root for README",
    ]


def test_render_event_cli_handles_action_kinds() -> None:
    events: list[TakopiEvent] = [
        action_completed(
            "c-1", "command", "pytest -q", ok=False, detail={"exit_code": 1}
        ),
        action_completed(
            "s-1",
            "web_search",
            "python jsonlines parser handle unknown fields",
            ok=True,
        ),
        action_completed("t-1", "tool", "github.search_issues", ok=True),
        action_completed(
            "f-1",
            "file_change",
            "2 files",
            ok=True,
            detail={
                "changes": [
                    {"path": "README.md", "kind": "add"},
                    {"path": "src/compute_answer.py", "kind": "update"},
                ]
            },
        ),
        action_completed("n-1", "note", "stream error", ok=False),
    ]

    out: list[str] = []
    for evt in events:
        out.extend(render_event_cli(evt))

    assert any(line.startswith("✗ `pytest -q` (exit 1)") for line in out)
    assert any(
        "searched: python jsonlines parser handle unknown fields" in line
        for line in out
    )
    assert any("tool: github.search_issues" in line for line in out)
    assert any(
        "files: add `README.md`, update `src/compute_answer.py`" in line for line in out
    )
    assert any(line.startswith("✗ stream error") for line in out)


def test_file_change_renders_relative_paths_inside_cwd() -> None:
    readme_abs = str(Path.cwd() / "README.md")
    weird_abs = "~" + readme_abs
    out = render_event_cli(
        action_completed(
            "f-abs",
            "file_change",
            "README.md",
            ok=True,
            detail={
                "changes": [
                    {"path": readme_abs, "kind": "update"},
                    {"path": weird_abs, "kind": "update"},
                ]
            },
        )
    )
    assert any(
        f"files: update `README.md`, update `{weird_abs}`" in line for line in out
    )


def test_progress_renderer_renders_progress_and_final() -> None:
    r = ExecProgressRenderer(
        max_actions=5, resume_formatter=_format_resume, engine="codex"
    )
    for evt in SAMPLE_EVENTS:
        r.note_event(evt)

    progress_parts = r.render_progress_parts(3.0)
    progress = assemble_markdown_parts(progress_parts)
    assert progress.startswith("working · codex · 3s · step 2")
    assert "✓ `bash -lc ls`" in progress
    assert "`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`" in progress

    final_parts = r.render_final_parts(3.0, "answer", status="done")
    final = assemble_markdown_parts(final_parts)
    assert final.startswith("done · codex · 3s · step 2")
    assert "✓ `bash -lc ls`" not in final
    assert "Checking repository root for README" not in final
    assert "answer" in final
    assert final.rstrip().endswith(
        "`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`"
    )


def test_progress_renderer_clamps_actions_and_ignores_unknown() -> None:
    r = ExecProgressRenderer(max_actions=3, command_width=20, engine="codex")
    events = [
        action_completed(
            f"item_{i}",
            "command",
            f"echo {i}",
            ok=True,
            detail={"exit_code": 0},
        )
        for i in range(6)
    ]

    for evt in events:
        assert r.note_event(evt) is True

    assert len(r.recent_actions) == 3
    assert "echo 3" in r.recent_actions[0]
    assert "echo 5" in r.recent_actions[-1]
    mystery = SimpleNamespace(type="mystery")
    assert r.note_event(cast(TakopiEvent, mystery)) is False


def test_progress_renderer_renders_commands_in_markdown() -> None:
    r = ExecProgressRenderer(max_actions=5, command_width=None, engine="codex")
    for i in (30, 31, 32):
        r.note_event(
            action_completed(
                f"item_{i}",
                "command",
                f"echo {i}",
                ok=True,
                detail={"exit_code": 0},
            )
        )

    md = assemble_markdown_parts(r.render_progress_parts(0.0))
    text, _ = render_markdown(md)
    assert "✓ echo 30" in text
    assert "✓ echo 31" in text
    assert "✓ echo 32" in text


def test_progress_renderer_handles_duplicate_action_ids() -> None:
    r = ExecProgressRenderer(max_actions=5, engine="codex")
    events = [
        action_started("dup", "command", "echo first"),
        action_completed(
            "dup",
            "command",
            "echo first",
            ok=True,
            detail={"exit_code": 0},
        ),
        action_started("dup", "command", "echo second"),
        action_completed(
            "dup",
            "command",
            "echo second",
            ok=True,
            detail={"exit_code": 0},
        ),
    ]

    for evt in events:
        assert r.note_event(evt) is True

    assert len(r.recent_actions) == 2
    assert r.recent_actions[0].startswith("✓ ")
    assert "echo first" in r.recent_actions[0]
    assert r.recent_actions[1].startswith("✓ ")
    assert "echo second" in r.recent_actions[1]


def test_progress_renderer_collapses_action_updates() -> None:
    r = ExecProgressRenderer(max_actions=5, engine="codex")
    events = [
        action_started("a-1", "command", "echo one"),
        action_started("a-1", "command", "echo two"),
        action_completed(
            "a-1",
            "command",
            "echo two",
            ok=True,
            detail={"exit_code": 0},
        ),
    ]

    for evt in events:
        assert r.note_event(evt) is True

    assert r.action_count == 1
    assert len(r.recent_actions) == 1
    assert r.recent_actions[0].startswith("✓ ")
    assert "echo two" in r.recent_actions[0]


def test_progress_renderer_deterministic_output() -> None:
    events = [
        action_started("a-1", "command", "echo ok"),
        action_completed(
            "a-1",
            "command",
            "echo ok",
            ok=True,
            detail={"exit_code": 0},
        ),
    ]
    r1 = ExecProgressRenderer(max_actions=5, engine="codex")
    r2 = ExecProgressRenderer(max_actions=5, engine="codex")

    for evt in events:
        r1.note_event(evt)
        r2.note_event(evt)

    assert assemble_markdown_parts(
        r1.render_progress_parts(1.0)
    ) == assemble_markdown_parts(r2.render_progress_parts(1.0))


def test_format_elapsed_branches() -> None:
    assert format_elapsed(3661) == "1h 01m"
    assert format_elapsed(61) == "1m 01s"
    assert format_elapsed(1.4) == "1s"


def test_shorten_and_action_status_branches() -> None:
    assert shorten("hello", None) == "hello"
    assert shorten("hello", 0) == ""
    shortened = shorten("hello world", 6)
    assert shortened.endswith("…")
    assert len(shortened) <= 6

    action_ok = Action(id="ok", kind="command", title="x", detail={"exit_code": 0})
    action_fail = Action(id="fail", kind="command", title="x", detail={"exit_code": 2})

    assert action_status(action_ok, completed=False, ok=None) == STATUS["running"]
    assert action_status(action_ok, completed=True, ok=None) == STATUS["done"]
    assert action_status(action_fail, completed=True, ok=None) == STATUS["fail"]


def test_format_file_change_title_handles_overflow_and_invalid() -> None:
    action = Action(
        id="f",
        kind="file_change",
        title="files",
        detail={
            "changes": [
                "bad",
                {"path": ""},
                {"path": "a", "kind": "add"},
                {"path": "b"},
                {"path": "c"},
                {"path": "d"},
            ]
        },
    )
    title = format_file_change_title(action, command_width=200)
    assert title.startswith("files: ")
    assert "…(" in title

    fallback = format_file_change_title(
        Action(id="empty", kind="file_change", title="all files"), command_width=50
    )
    assert fallback == "files: all files"


def test_render_event_cli_ignores_turn_actions() -> None:
    event = ActionEvent(
        engine="codex",
        action=Action(id="turn", kind="turn", title="turn"),
        phase="started",
        ok=None,
    )
    assert render_event_cli(event) == []


def test_progress_renderer_ignores_missing_action_id() -> None:
    renderer = ExecProgressRenderer(engine="codex")
    resume = ResumeToken(engine="codex", value="abc")
    renderer.note_event(StartedEvent(engine="codex", resume=resume, title="Session"))

    event = ActionEvent(
        engine="codex",
        action=Action(id="", kind="command", title="echo"),
        phase="started",
        ok=None,
    )
    assert renderer.note_event(event) is False

    header = assemble_markdown_parts(renderer.render_progress_parts(0.0))
    assert header.startswith("working · codex · 0s")


# --- Tests for split_body and prepare_telegram_split ---
# These functions handle splitting long final messages into multiple Telegram messages.
# Progress messages still use trim_body() via prepare_telegram() (see test_exec_bridge.py).


@pytest.mark.parametrize(
    "text,max_len,min_chunks",
    [
        ("short text", 100, 1),  # Short text fits in single chunk
        ("", 100, 0),  # Empty string returns empty list
        ("First paragraph.\n\nSecond paragraph.\n\nThird.", 30, 2),  # Paragraph split
        ("Line one\nLine two\nLine three\nLine four", 20, 2),  # Newline split
        ("word " * 20, 25, 2),  # Space split (100 chars total)
        ("x" * 100, 30, 4),  # Hard cut (no break points)
    ],
)
def test_split_body_chunking(text: str, max_len: int, min_chunks: int) -> None:
    result = split_body(text, max_len=max_len)
    assert len(result) >= min_chunks
    for chunk in result:
        assert len(chunk) <= max_len


def test_split_body_returns_empty_list_for_none() -> None:
    assert split_body(None, max_len=100) == []  # type: ignore


def test_split_body_hard_cut_exact_values() -> None:
    """Hard cuts produce exact chunk sizes when no break points exist."""
    text = "x" * 100
    result = split_body(text, max_len=30)

    assert len(result) == 4
    assert result[0] == "x" * 30
    assert result[1] == "x" * 30
    assert result[2] == "x" * 30
    assert result[3] == "x" * 10


def test_split_body_preserves_all_content() -> None:
    """Splitting preserves all content (nothing lost)."""
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    result = split_body(text, max_len=30)

    # All content should be preserved (ignoring whitespace changes from strip)
    assert "".join(result).replace(" ", "").replace("\n", "") == text.replace(
        " ", ""
    ).replace("\n", "")


def test_prepare_telegram_split_single_message_when_short() -> None:
    parts = MarkdownParts(header="Header", body="Short body", footer="Footer")
    result = prepare_telegram_split(parts)

    assert len(result) == 1
    text, _ = result[0]
    assert "Header" in text
    assert "Short body" in text
    assert "Footer" in text


def test_prepare_telegram_split_multiple_messages_when_long() -> None:
    long_body = "Q" * 8000  # Well over 4096 limit
    parts = MarkdownParts(header="done · 1s", body=long_body, footer="resume token")
    result = prepare_telegram_split(parts)

    assert len(result) >= 2, "Long body should split into multiple messages"

    # First message should have header
    first_text, _ = result[0]
    assert "done" in first_text

    # All messages should have footer (resume token)
    for text, _ in result:
        assert "resume token" in text

    # Continuation messages should have marker
    for text, _ in result[1:]:
        assert "continued" in text

    # All content should be preserved
    total_qs = sum(text.count("Q") for text, _ in result)
    assert total_qs == 8000


def test_prepare_telegram_split_respects_max_message_length() -> None:
    long_body = "word " * 2000  # Long text with spaces
    parts = MarkdownParts(header="Header", body=long_body, footer="Footer")
    result = prepare_telegram_split(parts, max_message_len=4096)

    # Each message should be under the limit
    for text, _ in result:
        assert len(text) <= 4096, f"Message length {len(text)} exceeds 4096"
