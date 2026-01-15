from takopi.model import ResumeToken
from takopi.runners.claude import ClaudeRunner
from takopi.runners.codex import CodexRunner
from takopi.runners.opencode import OpenCodeRunner, OpenCodeStreamState
from takopi.runners.pi import ENGINE as PI_ENGINE, PiRunner, PiStreamState
from takopi.runners.run_options import EngineRunOptions, apply_run_options


def test_codex_run_options_override_model_and_reasoning() -> None:
    runner = CodexRunner(codex_cmd="codex", extra_args=["-c", "notify=[]"])
    state = runner.new_state("hi", None)
    with apply_run_options(EngineRunOptions(model="gpt-4.1-mini", reasoning="low")):
        args = runner.build_args("hi", None, state=state)

    assert args == [
        "-c",
        "notify=[]",
        "--model",
        "gpt-4.1-mini",
        "-c",
        "model_reasoning_effort=low",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--color=never",
        "-",
    ]


def test_claude_run_options_override_model() -> None:
    runner = ClaudeRunner(claude_cmd="claude", model="claude-sonnet")
    with apply_run_options(EngineRunOptions(model="claude-opus")):
        args = runner.build_args("hi", None, state=None)

    assert "--model" in args
    model_idx = args.index("--model") + 1
    assert args[model_idx] == "claude-opus"


def test_opencode_run_options_override_model() -> None:
    runner = OpenCodeRunner(opencode_cmd="opencode", model="claude-sonnet")
    state = OpenCodeStreamState()
    with apply_run_options(EngineRunOptions(model="gpt-4o-mini")):
        args = runner.build_args("hi", None, state=state)

    assert "--model" in args
    model_idx = args.index("--model") + 1
    assert args[model_idx] == "gpt-4o-mini"


def test_pi_run_options_override_model() -> None:
    runner = PiRunner(extra_args=[], model="pi-default", provider=None)
    state = PiStreamState(resume=ResumeToken(engine=PI_ENGINE, value="sess.jsonl"))
    with apply_run_options(EngineRunOptions(model="pi-override")):
        args = runner.build_args("hi", None, state=state)

    assert "--model" in args
    model_idx = args.index("--model") + 1
    assert args[model_idx] == "pi-override"
