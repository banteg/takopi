import re
from collections.abc import AsyncIterator
from typing import Any

import pytest

import takopi.runner as runner_module
from takopi.model import (
    ActionEvent,
    CompletedEvent,
    ResumeToken,
    StartedEvent,
    TakopiEvent,
)
from takopi.runner import (
    BaseRunner,
    JsonlRunState,
    JsonlSubprocessRunner,
    ResumeTokenMixin,
)


class _DummyRunner(ResumeTokenMixin, BaseRunner):
    engine = "dummy"
    resume_re = re.compile(r"(?im)^`?dummy resume (?P<token>[^`\s]+)`?$")

    async def run_impl(
        self, prompt: str, resume: ResumeToken | None
    ) -> AsyncIterator[StartedEvent | CompletedEvent]:
        token = resume or ResumeToken(engine=self.engine, value="token")
        yield StartedEvent(engine=self.engine, resume=token, title="dummy")
        yield CompletedEvent(
            engine=self.engine,
            ok=True,
            answer=prompt,
            resume=token,
        )


class _DummyJsonlRunner(JsonlSubprocessRunner):
    engine = "dummy-jsonl"

    def command(self) -> str:
        return "dummy"

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: object,
    ) -> list[str]:
        _ = prompt, resume, state
        return []

    def translate(
        self,
        data: Any,
        *,
        state: Any,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        _ = data, state, resume, found_session
        return []


class _BareJsonlRunner(JsonlSubprocessRunner):
    engine = "bare-jsonl"


class _RunJsonlRunner(_DummyJsonlRunner):
    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        _ = prompt, resume, state
        return None

    async def iter_json_lines(self, stream: Any) -> AsyncIterator[bytes]:
        _ = stream
        yield b'{"type": "started", "resume": "sid"}'
        yield b'{"type": "completed", "resume": "sid"}'

    def translate(
        self,
        data: Any,
        *,
        state: Any,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        _ = state, resume, found_session
        token_value = "sid"
        if isinstance(data, dict) and isinstance(data.get("resume"), str):
            token_value = data["resume"]
        token = ResumeToken(engine=self.engine, value=token_value)
        if isinstance(data, dict) and data.get("type") == "started":
            return [StartedEvent(engine=self.engine, resume=token, title="t")]
        if isinstance(data, dict) and data.get("type") == "completed":
            return [
                CompletedEvent(engine=self.engine, ok=True, answer="done", resume=token)
            ]
        return []


class _BranchingJsonlRunner(_DummyJsonlRunner):
    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        _ = prompt, resume, state
        return None

    async def iter_json_lines(self, stream: Any) -> AsyncIterator[bytes]:
        _ = stream
        yield b"raise"
        yield b""
        yield b"invalid"
        yield b'{"type": "translate_error"}'
        yield b'{"type": "started", "resume": "sid"}'
        yield b'{"type": "started", "resume": "sid"}'
        yield b'{"type": "completed", "resume": "sid"}'
        yield b'{"type": "after"}'

    def decode_jsonl(self, *, line: bytes) -> Any | None:
        if line == b"raise":
            raise ValueError("boom")
        if line == b"invalid":
            return None
        return super().decode_jsonl(line=line)

    def translate(
        self,
        data: Any,
        *,
        state: Any,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[TakopiEvent]:
        _ = state, resume, found_session
        if isinstance(data, dict) and data.get("type") == "translate_error":
            raise RuntimeError("nope")
        token_value = "sid"
        if isinstance(data, dict) and isinstance(data.get("resume"), str):
            token_value = data["resume"]
        token = ResumeToken(engine=self.engine, value=token_value)
        if isinstance(data, dict) and data.get("type") == "started":
            return [StartedEvent(engine=self.engine, resume=token, title="t")]
        if isinstance(data, dict) and data.get("type") == "completed":
            return [
                CompletedEvent(engine=self.engine, ok=True, answer="done", resume=token)
            ]
        return []


@pytest.mark.anyio
async def test_base_runner_run_locked_handles_resume() -> None:
    runner = _DummyRunner()
    events = [evt async for evt in runner.run("hello", None)]
    assert isinstance(events[0], StartedEvent)
    assert isinstance(events[-1], CompletedEvent)

    resume = ResumeToken(engine=runner.engine, value="resume")
    resumed = [evt async for evt in runner.run("again", resume)]
    assert isinstance(resumed[0], StartedEvent)
    assert resumed[0].resume == resume


@pytest.mark.anyio
async def test_base_runner_rejects_wrong_resume_engine() -> None:
    runner = _DummyRunner()
    bad_resume = ResumeToken(engine="other", value="oops")
    with pytest.raises(RuntimeError):
        _ = [evt async for evt in runner.run("hello", bad_resume)]


@pytest.mark.anyio
async def test_base_runner_run_impl_not_implemented() -> None:
    class _BareRunner(BaseRunner):
        engine = "bare"

    runner = _BareRunner()
    with pytest.raises(NotImplementedError):
        _ = [evt async for evt in runner.run_impl("hello", None)]


def test_resume_token_format_and_extract() -> None:
    runner = _DummyRunner()
    token = ResumeToken(engine=runner.engine, value="abc")
    assert runner.format_resume(token) == "`dummy resume abc`"
    assert runner.is_resume_line("`dummy resume abc`") is True
    text = "`dummy resume first`\n`dummy resume second`"
    assert runner.extract_resume(text) == ResumeToken(
        engine=runner.engine, value="second"
    )
    assert runner.extract_resume(None) is None

    with pytest.raises(RuntimeError):
        runner.format_resume(ResumeToken(engine="other", value="bad"))


def test_session_lock_reuse() -> None:
    runner = _DummyRunner()
    token = ResumeToken(engine=runner.engine, value="one")
    lock1 = runner.lock_for(token)
    lock2 = runner.lock_for(token)
    other = runner.lock_for(ResumeToken(engine=runner.engine, value="two"))
    assert lock1 is lock2
    assert other is not lock1


@pytest.mark.anyio
async def test_run_with_resume_lock_passthrough() -> None:
    runner = _DummyRunner()
    events = [
        evt async for evt in runner.run_with_resume_lock("hello", None, runner.run_impl)
    ]
    assert events


def test_jsonl_helpers() -> None:
    runner = _DummyJsonlRunner()
    state = JsonlRunState()

    note1 = runner.next_note_id(state)
    note2 = runner.next_note_id(state)
    assert note1.endswith(".1")
    assert note2.endswith(".2")

    event = runner.note_event("warn", state=state)
    assert isinstance(event, ActionEvent)
    assert event.action.detail == {}

    invalid = runner.invalid_json_events(raw="x", line="{}", state=state)
    invalid_event = invalid[0]
    assert isinstance(invalid_event, ActionEvent)
    assert invalid_event.action.detail["line"] == "{}"

    assert runner.decode_jsonl(line=b'{"a": 1}') == {"a": 1}
    assert runner.decode_jsonl(line=b"{") is None

    err_events = runner.decode_error_events(
        raw="oops", line="{}", error=ValueError("nope"), state=state
    )
    err_event = err_events[0]
    assert isinstance(err_event, ActionEvent)
    assert err_event.action.detail["error"] == "nope"

    translated = runner.translate_error_events(
        data={"type": "foo", "item": {"type": "bar"}},
        error=ValueError("boom"),
        state=state,
    )
    translated_event = translated[0]
    assert isinstance(translated_event, ActionEvent)
    detail = translated_event.action.detail
    assert detail["type"] == "foo"
    assert detail["item_type"] == "bar"

    resume = ResumeToken(engine=runner.engine, value="sid")
    processed = runner.process_error_events(
        2, resume=resume, found_session=None, state=state
    )
    processed_event = processed[-1]
    assert isinstance(processed_event, CompletedEvent)
    assert processed_event.ok is False
    assert processed_event.resume == resume

    stream_end = runner.stream_end_events(
        resume=None, found_session=resume, state=state
    )
    stream_event = stream_end[-1]
    assert isinstance(stream_event, CompletedEvent)
    assert stream_event.resume == resume

    started = StartedEvent(engine=runner.engine, resume=resume, title="t")
    found, emit = runner.handle_started_event(
        started, expected_session=None, found_session=None
    )
    assert found == resume
    assert emit is True

    found, emit = runner.handle_started_event(
        started, expected_session=None, found_session=resume
    )
    assert found == resume
    assert emit is False

    mismatch = StartedEvent(engine="other", resume=resume, title="t")
    with pytest.raises(RuntimeError):
        runner.handle_started_event(mismatch, expected_session=None, found_session=None)

    other_resume = ResumeToken(engine=runner.engine, value="other")
    with pytest.raises(RuntimeError):
        runner.handle_started_event(
            StartedEvent(engine=runner.engine, resume=other_resume, title="t"),
            expected_session=resume,
            found_session=None,
        )

    with pytest.raises(RuntimeError):
        runner.handle_started_event(
            StartedEvent(engine=runner.engine, resume=other_resume, title="t"),
            expected_session=None,
            found_session=resume,
        )


def test_next_note_id_requires_state_field() -> None:
    runner = _DummyJsonlRunner()
    with pytest.raises(RuntimeError):
        runner.next_note_id(object())


def test_jsonl_base_methods_raise_and_defaults() -> None:
    runner = _BareJsonlRunner()
    with pytest.raises(NotImplementedError):
        runner.command()
    with pytest.raises(NotImplementedError):
        runner.build_args("hi", None, state=None)
    with pytest.raises(NotImplementedError):
        runner.translate(data={}, state=None, resume=None, found_session=None)
    assert runner.pipes_error_message().startswith("bare-jsonl")
    state = runner.new_state("hi", None)
    assert isinstance(state, JsonlRunState)
    assert runner.start_run("hi", None, state=state) is None
    assert runner.env(state=state) is None
    assert runner.stdin_payload("hi", None, state=state) == b"hi"


@pytest.mark.anyio
async def test_jsonl_run_impl_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = object()
            self.stderr = object()
            self.stdin = None
            self.pid = 123

        async def wait(self) -> int:
            return 0

    class _FakeManager:
        def __init__(self, proc: _FakeProc) -> None:
            self._proc = proc

        async def __aenter__(self) -> _FakeProc:
            return self._proc

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    proc = _FakeProc()

    def fake_manage_subprocess(*args: Any, **kwargs: Any) -> _FakeManager:
        _ = args, kwargs
        return _FakeManager(proc)

    async def fake_drain_stderr(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        return None

    monkeypatch.setattr(runner_module, "manage_subprocess", fake_manage_subprocess)
    monkeypatch.setattr(runner_module, "drain_stderr", fake_drain_stderr)

    runner = _RunJsonlRunner()
    events = [evt async for evt in runner.run_impl("hello", None)]
    assert any(isinstance(evt, CompletedEvent) for evt in events)


@pytest.mark.anyio
async def test_jsonl_run_impl_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = object()
            self.stderr = object()
            self.stdin = None
            self.pid = 456

        async def wait(self) -> int:
            return 0

    class _FakeManager:
        def __init__(self, proc: _FakeProc) -> None:
            self._proc = proc

        async def __aenter__(self) -> _FakeProc:
            return self._proc

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    proc = _FakeProc()

    def fake_manage_subprocess(*args: Any, **kwargs: Any) -> _FakeManager:
        _ = args, kwargs
        return _FakeManager(proc)

    async def fake_drain_stderr(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        return None

    monkeypatch.setattr(runner_module, "manage_subprocess", fake_manage_subprocess)
    monkeypatch.setattr(runner_module, "drain_stderr", fake_drain_stderr)

    runner = _BranchingJsonlRunner()
    events = [evt async for evt in runner.run_impl("hello", None)]
    assert any(isinstance(evt, CompletedEvent) for evt in events)
