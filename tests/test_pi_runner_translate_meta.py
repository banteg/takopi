from pathlib import Path
from unittest.mock import patch

from takopi.model import ResumeToken, StartedEvent
from takopi.runners.pi import ENGINE, PiRunner, PiStreamState
from takopi.schemas import pi as pi_schema


def test_translate_meta_includes_run_base_dir_only_for_fresh_sessions() -> None:
    runner = PiRunner(extra_args=[], model=None, provider=None)

    with patch(
        "takopi.utils.paths.get_run_base_dir", return_value=Path("/project")
    ):
        state = PiStreamState(resume=ResumeToken(engine=ENGINE, value="session.jsonl"))
        events = runner.translate(
            pi_schema.MessageEnd(message={"role": "assistant", "content": []}),
            state=state,
            resume=None,
            found_session=None,
        )

    started = next(evt for evt in events if isinstance(evt, StartedEvent))
    assert started.meta is not None
    assert started.meta["run_base_dir"] == "/project"

    with patch(
        "takopi.utils.paths.get_run_base_dir",
        return_value=Path("/should-not-be-used"),
    ):
        resumed_state = PiStreamState(
            resume=ResumeToken(engine=ENGINE, value="session.jsonl")
        )
        resumed_events = runner.translate(
            pi_schema.MessageEnd(message={"role": "assistant", "content": []}),
            state=resumed_state,
            resume=ResumeToken(engine=ENGINE, value="existing.jsonl"),
            found_session=None,
        )

    resumed_started = next(evt for evt in resumed_events if isinstance(evt, StartedEvent))
    assert resumed_started.meta is not None
    assert "run_base_dir" not in resumed_started.meta
