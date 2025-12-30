import json
from pathlib import Path

from takopi.exec_render import ExecProgressRenderer, render_event_cli, render_markdown


def _loads(lines: str) -> list[dict]:
    return [json.loads(line) for line in lines.strip().splitlines() if line.strip()]


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "codex.jsonl"
ALL_FORMATS_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "codex_exec_json_all_formats.jsonl"
)
ALL_FORMATS_GOLDEN_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "codex_exec_json_all_formats.txt"
)

SAMPLE_STREAM = """
{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"reasoning","text":"**Searching for README files**"}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"bash -lc ls","aggregated_output":"","status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"bash -lc ls","aggregated_output":"2025-09-11\\nAGENTS.md\\nCHANGELOG.md\\ncliff.toml\\ncodex-cli\\ncodex-rs\\ndocs\\nexamples\\nflake.lock\\nflake.nix\\nLICENSE\\nnode_modules\\nNOTICE\\npackage.json\\npnpm-lock.yaml\\npnpm-workspace.yaml\\nPNPM.md\\nREADME.md\\nscripts\\nsdk\\ntmp\\n","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_2","type":"reasoning","text":"**Checking repository root for README**"}}
{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"Yep — there’s a `README.md` in the repository root."}}
{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}
"""


def test_render_event_cli_sample_stream() -> None:
    last_turn = None
    out: list[str] = []
    for evt in _loads(SAMPLE_STREAM):
        last_turn, lines = render_event_cli(evt, last_turn)
        out.extend(lines)

    assert out == [
        "thread started",
        "turn started",
        "0. **Searching for README files**",
        "1. ▸ `bash -lc ls`",
        "1. ✓ `bash -lc ls`",
        "2. **Checking repository root for README**",
        "assistant:",
        "  Yep — there’s a `README.md` in the repository root.",
        "turn completed",
    ]


def test_render_event_cli_real_run_fixture() -> None:
    events = _loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    last_turn = None
    out: list[str] = []
    for evt in events:
        last_turn, lines = render_event_cli(evt, last_turn)
        out.extend(lines)

    print("\n".join(out))

    assert out[0] == "thread started"
    assert "turn started" in out
    assert any(line.startswith("0. ▸ `") for line in out)
    assert any(line.startswith("0. ✓ `") for line in out)
    assert "assistant:" in out
    assert any("takopi" in line for line in out)
    assert out[-1] == "turn completed"


def test_render_event_cli_all_formats_fixture() -> None:
    events = _loads(ALL_FORMATS_FIXTURE_PATH.read_text(encoding="utf-8"))
    last_turn = None
    out: list[str] = []
    for evt in events:
        last_turn, lines = render_event_cli(evt, last_turn)
        out.extend(lines)

    assert "thread started" in out
    assert "turn started" in out
    assert any(line.startswith("stream error:") for line in out)
    assert any(line.startswith("4. ▸ `pytest -q`") for line in out)
    assert any("✗ `pytest -q` (exit 1)" in line for line in out)
    assert any(
        "searched: python jsonlines parser handle unknown fields" in line
        for line in out
    )
    assert any("tool: github.search_issues" in line for line in out)
    assert any("updated `src/compute_answer.py`" in line for line in out)
    assert any(
        line.startswith(
            "turn failed: Aborted: required dependency `npm` is missing; cannot continue."
        )
        for line in out
    )
    assert "assistant:" in out
    assert any("Legacy schema example" in line for line in out)


def test_render_event_cli_all_formats_golden() -> None:
    events = _loads(ALL_FORMATS_FIXTURE_PATH.read_text(encoding="utf-8"))
    last_turn = None
    out: list[str] = []
    for evt in events:
        last_turn, lines = render_event_cli(evt, last_turn)
        out.extend(lines)

    print("\n".join(out))

    expected = ALL_FORMATS_GOLDEN_PATH.read_text(encoding="utf-8").rstrip("\n")
    assert "\n".join(out) == expected


def test_progress_renderer_renders_progress_and_final() -> None:
    r = ExecProgressRenderer(max_actions=5)
    for evt in _loads(SAMPLE_STREAM):
        r.note_event(evt)

    progress = r.render_progress(3.0)
    assert progress.startswith("working · 3s · step 3")
    assert "1\\. ✓ `bash -lc ls`" in progress
    assert "resume: `0199a213-81c0-7800-8aa1-bbab2a035a53`" in progress

    final = r.render_final(3.0, "answer", status="done")
    assert final.startswith("done · 3s · step 3")
    assert "running:" not in final
    assert "ran:" not in final
    assert "answer" in final
    assert final.rstrip().endswith("resume: `0199a213-81c0-7800-8aa1-bbab2a035a53`")


def test_progress_renderer_clamps_actions_and_ignores_unknown() -> None:
    r = ExecProgressRenderer(max_actions=3, command_width=20)
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": f"item_{i}",
                "type": "command_execution",
                "command": f"echo {i}",
                "exit_code": 0,
                "status": "completed",
            },
        }
        for i in range(6)
    ]

    for evt in events:
        assert r.note_event(evt) is True

    assert len(r.recent_actions) == 3
    assert r.recent_actions[0].startswith("3\\. ")
    assert r.recent_actions[-1].startswith("5\\. ")
    assert r.note_event({"type": "mystery"}) is False


def test_progress_renderer_preserves_item_ids_in_telegram_text() -> None:
    r = ExecProgressRenderer(max_actions=5, command_width=None)
    for i in (30, 31, 32):
        r.note_event(
            {
                "type": "item.completed",
                "item": {
                    "id": f"item_{i}",
                    "type": "command_execution",
                    "command": f"echo {i}",
                    "exit_code": 0,
                    "status": "completed",
                },
            }
        )

    md = r.render_progress(0.0)
    assert "30\\." in md
    assert "31\\." in md
    assert "32\\." in md

    text, _ = render_markdown(md)
    assert "30. ✓ echo 30" in text
    assert "31. ✓ echo 31" in text
    assert "32. ✓ echo 32" in text
