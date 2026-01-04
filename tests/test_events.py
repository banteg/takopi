import pytest

from takopi.events import EventFactory
from takopi.model import ResumeToken


class TestEventFactory:
    def test_init(self) -> None:
        factory = EventFactory("codex")
        assert factory.engine == "codex"
        assert factory.resume is None

    def test_started_sets_resume(self) -> None:
        factory = EventFactory("codex")
        token = ResumeToken(engine="codex", value="abc123")

        event = factory.started(token, title="Test")

        assert factory.resume == token
        assert event.engine == "codex"
        assert event.resume == token
        assert event.title == "Test"

    def test_started_engine_mismatch_raises(self) -> None:
        factory = EventFactory("codex")
        token = ResumeToken(engine="claude", value="abc123")

        with pytest.raises(RuntimeError, match="resume token is for engine"):
            factory.started(token)

    def test_started_resume_mismatch_raises(self) -> None:
        factory = EventFactory("codex")
        token1 = ResumeToken(engine="codex", value="abc123")
        token2 = ResumeToken(engine="codex", value="def456")

        factory.started(token1)
        with pytest.raises(RuntimeError, match="resume token mismatch"):
            factory.started(token2)

    def test_action_started(self) -> None:
        factory = EventFactory("codex")
        event = factory.action_started(
            action_id="a1",
            kind="tool",
            title="Running command",
            detail={"cmd": "ls"},
        )

        assert event.phase == "started"
        assert event.action.id == "a1"
        assert event.action.kind == "tool"
        assert event.action.title == "Running command"
        assert event.action.detail == {"cmd": "ls"}

    def test_action_updated(self) -> None:
        factory = EventFactory("codex")
        event = factory.action_updated(
            action_id="a1",
            kind="tool",
            title="Still running",
        )

        assert event.phase == "updated"
        assert event.action.id == "a1"

    def test_action_completed(self) -> None:
        factory = EventFactory("codex")
        event = factory.action_completed(
            action_id="a1",
            kind="tool",
            title="Finished",
            ok=True,
            message="Success",
            level="info",
        )

        assert event.phase == "completed"
        assert event.ok is True
        assert event.message == "Success"
        assert event.level == "info"

    def test_completed_uses_stored_resume(self) -> None:
        factory = EventFactory("codex")
        token = ResumeToken(engine="codex", value="abc123")
        factory.started(token)

        event = factory.completed(ok=True, answer="Done")

        assert event.resume == token

    def test_completed_ok(self) -> None:
        factory = EventFactory("codex")
        event = factory.completed_ok(answer="All good", usage={"tokens": 100})

        assert event.ok is True
        assert event.answer == "All good"
        assert event.usage == {"tokens": 100}

    def test_completed_error(self) -> None:
        factory = EventFactory("codex")
        event = factory.completed_error(error="Something went wrong")

        assert event.ok is False
        assert event.error == "Something went wrong"
        assert event.answer == ""
