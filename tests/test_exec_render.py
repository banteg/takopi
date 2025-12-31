from typing import cast

from takopi.exec_render import ExecProgressRenderer, render_event_cli, render_markdown
from takopi.model import ResumeToken, TakopiEvent


def _format_resume(token: ResumeToken) -> str:
    return f"`codex resume {token.value}`"


SAMPLE_EVENTS = [
    {
        "type": "session.started",
        "engine": "codex",
        "resume": ResumeToken(
            engine="codex", value="0199a213-81c0-7800-8aa1-bbab2a035a53"
        ),
        "title": "Codex",
    },
    {
        "type": "action.started",
        "engine": "codex",
        "action": {
            "id": "a-1",
            "kind": "command",
            "title": "bash -lc ls",
            "detail": {},
        },
    },
    {
        "type": "action.completed",
        "engine": "codex",
        "action": {
            "id": "a-1",
            "kind": "command",
            "title": "bash -lc ls",
            "detail": {"exit_code": 0},
        },
        "ok": True,
    },
    {
        "type": "action.completed",
        "engine": "codex",
        "action": {
            "id": "a-2",
            "kind": "note",
            "title": "Checking repository root for README",
            "detail": {},
        },
        "ok": True,
    },
]


def test_render_event_cli_sample_events() -> None:
    last = None
    out: list[str] = []
    for evt in SAMPLE_EVENTS:
        last, lines = render_event_cli(cast(TakopiEvent, evt), last)
        out.extend(lines)

    assert out == [
        "codex",
        "▸ `bash -lc ls`",
        "✓ `bash -lc ls`",
        "✓ Checking repository root for README",
    ]


def test_render_event_cli_handles_action_kinds() -> None:
    events = [
        {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": "c-1",
                "kind": "command",
                "title": "pytest -q",
                "detail": {"exit_code": 1},
            },
            "ok": False,
        },
        {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": "s-1",
                "kind": "web_search",
                "title": "python jsonlines parser handle unknown fields",
                "detail": {},
            },
            "ok": True,
        },
        {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": "t-1",
                "kind": "tool",
                "title": "github.search_issues",
                "detail": {},
            },
            "ok": True,
        },
        {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": "f-1",
                "kind": "file_change",
                "title": "src/compute_answer.py",
                "detail": {},
            },
            "ok": True,
        },
        {
            "type": "error",
            "engine": "codex",
            "message": "stream error",
        },
    ]

    last = None
    out: list[str] = []
    for evt in events:
        last, lines = render_event_cli(cast(TakopiEvent, evt), last)
        out.extend(lines)

    assert any(line.startswith("✗ `pytest -q` (exit 1)") for line in out)
    assert any(
        "searched: python jsonlines parser handle unknown fields" in line
        for line in out
    )
    assert any("tool: github.search_issues" in line for line in out)
    assert any("updated src/compute_answer.py" in line for line in out)
    assert any(line.startswith("error: stream error") for line in out)


def test_progress_renderer_renders_progress_and_final() -> None:
    r = ExecProgressRenderer(max_actions=5, resume_formatter=_format_resume)
    for evt in SAMPLE_EVENTS:
        r.note_event(cast(TakopiEvent, evt))

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
        {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": f"item_{i}",
                "kind": "command",
                "title": f"echo {i}",
                "detail": {"exit_code": 0},
            },
            "ok": True,
        }
        for i in range(6)
    ]

    for evt in events:
        assert r.note_event(cast(TakopiEvent, evt)) is True

    assert len(r.recent_actions) == 3
    assert "echo 3" in r.recent_actions[0]
    assert "echo 5" in r.recent_actions[-1]
    assert (
        r.note_event(cast(TakopiEvent, {"type": "mystery", "engine": "codex"})) is False
    )


def test_progress_renderer_renders_commands_in_markdown() -> None:
    r = ExecProgressRenderer(max_actions=5, command_width=None)
    for i in (30, 31, 32):
        evt = {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": f"item_{i}",
                "kind": "command",
                "title": f"echo {i}",
                "detail": {"exit_code": 0},
            },
            "ok": True,
        }
        r.note_event(cast(TakopiEvent, evt))

    md = r.render_progress(0.0)
    text, _ = render_markdown(md)
    assert "✓ echo 30" in text
    assert "✓ echo 31" in text
    assert "✓ echo 32" in text


def test_progress_renderer_handles_duplicate_action_ids() -> None:
    r = ExecProgressRenderer(max_actions=5)
    events = [
        {
            "type": "action.started",
            "engine": "codex",
            "action": {
                "id": "dup",
                "kind": "command",
                "title": "echo first",
                "detail": {},
            },
        },
        {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": "dup",
                "kind": "command",
                "title": "echo first",
                "detail": {"exit_code": 0},
            },
            "ok": True,
        },
        {
            "type": "action.started",
            "engine": "codex",
            "action": {
                "id": "dup",
                "kind": "command",
                "title": "echo second",
                "detail": {},
            },
        },
        {
            "type": "action.completed",
            "engine": "codex",
            "action": {
                "id": "dup",
                "kind": "command",
                "title": "echo second",
                "detail": {"exit_code": 0},
            },
            "ok": True,
        },
    ]

    for evt in events:
        assert r.note_event(cast(TakopiEvent, evt)) is True

    assert len(r.recent_actions) == 4
    assert r.recent_actions[0].startswith("▸ ")
    assert "echo first" in r.recent_actions[0]
    assert r.recent_actions[1].startswith("✓ ")
    assert "echo first" in r.recent_actions[1]
    assert r.recent_actions[2].startswith("▸ ")
    assert "echo second" in r.recent_actions[2]
    assert r.recent_actions[3].startswith("✓ ")
    assert "echo second" in r.recent_actions[3]
