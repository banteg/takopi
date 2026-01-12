from typing import cast
from types import SimpleNamespace
from pathlib import Path

from takopi.markdown import (
    HARD_BREAK,
    MarkdownFormatter,
    STATUS,
    action_status,
    assemble_markdown_parts,
    format_elapsed,
    format_file_change_title,
    render_event_cli,
    shorten,
)
from takopi.model import Action, ActionEvent, ResumeToken, StartedEvent, TakopiEvent
from takopi.progress import ProgressTracker
from takopi.telegram.render import render_markdown
from takopi.utils.paths import reset_run_base_dir, set_run_base_dir
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


def test_file_change_renders_change_objects(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    abs_path = str(base / "changelog.md")
    token = set_run_base_dir(base)
    try:
        out = render_event_cli(
            action_completed(
                "f-obj",
                "file_change",
                "ignored",
                ok=True,
                detail={"changes": [SimpleNamespace(path=abs_path, kind="update")]},
            )
        )
    finally:
        reset_run_base_dir(token)
    assert any("files: update `changelog.md`" in line for line in out)


def test_file_change_title_relativizes_absolute_title(tmp_path: Path) -> None:
    base = tmp_path / "repo"
    base.mkdir()
    abs_path = str(base / "changelog.md")
    token = set_run_base_dir(base)
    try:
        out = render_event_cli(
            action_completed("f-abs", "file_change", abs_path, ok=True)
        )
    finally:
        reset_run_base_dir(token)
    assert any("files: `changelog.md`" in line for line in out)


def test_progress_renderer_renders_progress_and_final() -> None:
    tracker = ProgressTracker(engine="codex")
    for evt in SAMPLE_EVENTS:
        tracker.note_event(evt)

    state = tracker.snapshot(resume_formatter=_format_resume)
    formatter = MarkdownFormatter(max_actions=5)
    progress_parts = formatter.render_progress_parts(state, elapsed_s=3.0)
    progress = assemble_markdown_parts(progress_parts)
    assert progress.startswith("working · codex · 3s · step 2")
    assert "✓ `bash -lc ls`" in progress
    assert "`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`" in progress

    final_parts = formatter.render_final_parts(
        state, elapsed_s=3.0, status="done", answer="answer"
    )
    final = assemble_markdown_parts(final_parts)
    assert final.startswith("done · codex · 3s · step 2")
    assert "✓ `bash -lc ls`" not in final
    assert "Checking repository root for README" not in final
    assert "answer" in final
    assert final.rstrip().endswith(
        "`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`"
    )


def test_progress_renderer_footer_includes_ctx_before_resume() -> None:
    tracker = ProgressTracker(engine="codex")
    for evt in SAMPLE_EVENTS:
        tracker.note_event(evt)

    state = tracker.snapshot(
        resume_formatter=_format_resume,
        context_line="`ctx: z80 @feat/name`",
    )
    formatter = MarkdownFormatter(max_actions=5)
    parts = formatter.render_progress_parts(state, elapsed_s=0.0)
    assert parts.footer == (
        "`ctx: z80 @feat/name`"
        f"{HARD_BREAK}`codex resume 0199a213-81c0-7800-8aa1-bbab2a035a53`"
    )


def test_progress_renderer_clamps_actions_and_ignores_unknown() -> None:
    tracker = ProgressTracker(engine="codex")
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
        assert tracker.note_event(evt) is True

    state = tracker.snapshot()
    formatter = MarkdownFormatter(max_actions=3, command_width=20)
    parts = formatter.render_progress_parts(state, elapsed_s=0.0)
    lines = parts.body.split(HARD_BREAK) if parts.body else []
    assert len(lines) == 3
    assert "echo 3" in lines[0]
    assert "echo 5" in lines[-1]
    mystery = SimpleNamespace(type="mystery")
    assert tracker.note_event(cast(TakopiEvent, mystery)) is False


def test_progress_renderer_renders_commands_in_markdown() -> None:
    tracker = ProgressTracker(engine="codex")
    for i in (30, 31, 32):
        tracker.note_event(
            action_completed(
                f"item_{i}",
                "command",
                f"echo {i}",
                ok=True,
                detail={"exit_code": 0},
            )
        )

    state = tracker.snapshot()
    formatter = MarkdownFormatter(max_actions=5, command_width=None)
    md = assemble_markdown_parts(formatter.render_progress_parts(state, elapsed_s=0.0))
    text, _ = render_markdown(md)
    assert "✓ echo 30" in text
    assert "✓ echo 31" in text
    assert "✓ echo 32" in text


def test_progress_renderer_handles_duplicate_action_ids() -> None:
    tracker = ProgressTracker(engine="codex")
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
        assert tracker.note_event(evt) is True

    state = tracker.snapshot()
    formatter = MarkdownFormatter(max_actions=5)
    parts = formatter.render_progress_parts(state, elapsed_s=0.0)
    lines = parts.body.split(HARD_BREAK) if parts.body else []
    assert len(lines) == 1
    assert lines[0].startswith("✓ ")
    assert "echo second" in lines[0]


def test_progress_renderer_collapses_action_updates() -> None:
    tracker = ProgressTracker(engine="codex")
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
        assert tracker.note_event(evt) is True

    assert tracker.action_count == 1
    state = tracker.snapshot()
    formatter = MarkdownFormatter(max_actions=5)
    parts = formatter.render_progress_parts(state, elapsed_s=0.0)
    lines = parts.body.split(HARD_BREAK) if parts.body else []
    assert len(lines) == 1
    assert lines[0].startswith("✓ ")
    assert "echo two" in lines[0]


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
    t1 = ProgressTracker(engine="codex")
    t2 = ProgressTracker(engine="codex")

    for evt in events:
        t1.note_event(evt)
        t2.note_event(evt)

    f1 = MarkdownFormatter(max_actions=5)
    f2 = MarkdownFormatter(max_actions=5)
    assert assemble_markdown_parts(
        f1.render_progress_parts(t1.snapshot(), elapsed_s=1.0)
    ) == assemble_markdown_parts(f2.render_progress_parts(t2.snapshot(), elapsed_s=1.0))


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
    tracker = ProgressTracker(engine="codex")
    resume = ResumeToken(engine="codex", value="abc")
    tracker.note_event(StartedEvent(engine="codex", resume=resume, title="Session"))

    event = ActionEvent(
        engine="codex",
        action=Action(id="", kind="command", title="echo"),
        phase="started",
        ok=None,
    )
    assert tracker.note_event(event) is False

    formatter = MarkdownFormatter()
    header = assemble_markdown_parts(
        formatter.render_progress_parts(tracker.snapshot(), elapsed_s=0.0)
    )
    assert header.startswith("working · codex · 0s")
