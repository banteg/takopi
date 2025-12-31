from typing import cast

from takopi.markdown import render_markdown
from takopi.model import TakopiEvent
from takopi.render import ExecProgressRenderer, render_event_cli
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
    last = None
    out: list[str] = []
    for evt in SAMPLE_EVENTS:
        last, lines = render_event_cli(evt, last)
        out.extend(lines)

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
        action_completed("f-1", "file_change", "src/compute_answer.py", ok=True),
        action_completed("n-1", "note", "stream error", ok=False),
    ]

    last = None
    out: list[str] = []
    for evt in events:
        last, lines = render_event_cli(evt, last)
        out.extend(lines)

    assert any(line.startswith("✗ `pytest -q` (exit 1)") for line in out)
    assert any(
        "searched: python jsonlines parser handle unknown fields" in line
        for line in out
    )
    assert any("tool: github.search_issues" in line for line in out)
    assert any("updated src/compute_answer.py" in line for line in out)
    assert any(line.startswith("✗ stream error") for line in out)


def test_progress_renderer_renders_progress_and_final() -> None:
    r = ExecProgressRenderer(max_actions=5, resume_formatter=_format_resume)
    for evt in SAMPLE_EVENTS:
        r.note_event(evt)

    progress = r.render_progress(3.0)
    assert progress.startswith("working · 3s · step 2")
    assert "✓ `bash -lc ls`" in progress
    assert "`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`" in progress

    final = r.render_final(3.0, "answer", status="done")
    assert final.startswith("done · 3s · step 2")
    assert "answer" in final
    assert final.rstrip().endswith(
        "`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`"
    )


def test_progress_renderer_clamps_actions_and_ignores_unknown() -> None:
    r = ExecProgressRenderer(max_actions=3, command_width=20)
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
    assert (
        r.note_event(cast(TakopiEvent, {"type": "mystery", "engine": "codex"})) is False
    )


def test_progress_renderer_renders_commands_in_markdown() -> None:
    r = ExecProgressRenderer(max_actions=5, command_width=None)
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

    md = r.render_progress(0.0)
    text, _ = render_markdown(md)
    assert "✓ echo 30" in text
    assert "✓ echo 31" in text
    assert "✓ echo 32" in text


def test_progress_renderer_handles_duplicate_action_ids() -> None:
    r = ExecProgressRenderer(max_actions=5)
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
    r1 = ExecProgressRenderer(max_actions=5)
    r2 = ExecProgressRenderer(max_actions=5)

    for evt in events:
        r1.note_event(evt)
        r2.note_event(evt)

    assert r1.render_progress(1.0) == r2.render_progress(1.0)
