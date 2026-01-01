from typing import cast
from types import SimpleNamespace
from pathlib import Path

from takopi.model import TakopiEvent
from takopi.render import (
    ExecProgressRenderer,
    assemble_markdown_parts,
    render_event_cli,
    render_markdown,
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
    r = ExecProgressRenderer(max_actions=5, resume_formatter=_format_resume)
    for evt in SAMPLE_EVENTS:
        r.note_event(evt)

    progress_parts = r.render_progress_parts(3.0)
    progress = assemble_markdown_parts(progress_parts)
    assert progress.startswith("working · 3s · step 2")
    assert "✓ `bash -lc ls`" in progress
    assert "`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`" in progress

    final_parts = r.render_final_parts(3.0, "answer", status="done")
    final = assemble_markdown_parts(final_parts)
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
    mystery = SimpleNamespace(type="mystery")
    assert r.note_event(cast(TakopiEvent, mystery)) is False


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

    md = assemble_markdown_parts(r.render_progress_parts(0.0))
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


def test_progress_renderer_collapses_action_updates() -> None:
    r = ExecProgressRenderer(max_actions=5)
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
    r1 = ExecProgressRenderer(max_actions=5)
    r2 = ExecProgressRenderer(max_actions=5)

    for evt in events:
        r1.note_event(evt)
        r2.note_event(evt)

    assert assemble_markdown_parts(
        r1.render_progress_parts(1.0)
    ) == assemble_markdown_parts(r2.render_progress_parts(1.0))
