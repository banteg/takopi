import pytest
import anyio
from takopi.runner_bridge import ExecBridgeConfig, IncomingMessage, handle_message
from takopi.markdown import MarkdownPresenter
from takopi.model import TakopiEvent
from takopi.runners.mock import Advance, Emit, Return, ScriptRunner, Sleep
from takopi.transport import MessageRef, RenderedMessage, SendOptions
from tests.factories import action_started

class FakeTransport:
    def __init__(self) -> None:
        self._next_id = 1
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.delete_calls: list[MessageRef] = []
        self.action_calls: list[dict] = []

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        ref = MessageRef(channel_id=channel_id, message_id=self._next_id)
        self._next_id += 1
        self.send_calls.append(
            {
                "ref": ref,
                "channel_id": channel_id,
                "message": message,
                "options": options,
            }
        )
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        self.edit_calls.append({"ref": ref, "message": message, "wait": wait})
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        self.delete_calls.append(ref)
        return True

    async def send_action(
        self,
        *,
        channel_id: int | str,
        action: str = "typing",
        thread_id: int | str | None = None,
    ) -> bool:
        self.action_calls.append({"channel_id": channel_id, "action": action})
        return True

    async def close(self) -> None:
        return None

class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def set(self, value: float) -> None:
        self._now = value

@pytest.mark.anyio
async def test_progress_full_updates() -> None:
    transport = FakeTransport()
    clock = _FakeClock()
    events = [action_started("item_0", "cmd", "echo 1")]
    runner = ScriptRunner(
        [Emit(events[0], at=0.2), Advance(1.0), Return("ok")],
        engine="mock",
        advance=clock.set,
    )
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
        progress_updates="full",
        show_typing=False,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="hi"),
        resume_token=None,
        clock=clock,
    )

    # Should have initial send + edits
    assert len(transport.send_calls) >= 1
    assert "starting" in transport.send_calls[0]["message"].text
    assert transport.edit_calls
    assert "working" in transport.edit_calls[-1]["message"].text

@pytest.mark.anyio
async def test_progress_once_no_intermediate_edits() -> None:
    transport = FakeTransport()
    clock = _FakeClock()
    events = [action_started("item_0", "cmd", "echo 1")]
    runner = ScriptRunner(
        [Emit(events[0], at=0.2), Advance(1.0), Return("ok")],
        engine="mock",
        advance=clock.set,
    )
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
        progress_updates="once",
        show_typing=False,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="hi"),
        resume_token=None,
        clock=clock,
    )

    # Should have initial send
    assert len(transport.send_calls) >= 1
    assert "starting" in transport.send_calls[0]["message"].text
    
    # But NO edits during run (except maybe final if final_notify is False, but here True)
    # The runner bridge logic does final edit/send.
    # We want to ensure NO intermediate edits that say "working".
    working_edits = [
        c for c in transport.edit_calls 
        if "working" in c["message"].text and "done" not in c["message"].text
    ]
    assert not working_edits

@pytest.mark.anyio
async def test_progress_none_sends_no_initial() -> None:
    transport = FakeTransport()
    runner = ScriptRunner([Return("ok")], engine="mock")
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
        progress_updates="none",
        show_typing=False,
    )

    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="hi"),
        resume_token=None,
    )

    # No initial "starting" message
    initial_sends = [c for c in transport.send_calls if "starting" in c["message"].text]
    assert not initial_sends
    # Should send final result
    assert transport.send_calls
    assert "done" in transport.send_calls[-1]["message"].text or "ok" in transport.send_calls[-1]["message"].text

@pytest.mark.anyio
async def test_show_typing_sends_actions() -> None:
    transport = FakeTransport()
    runner = ScriptRunner([Sleep(0.1), Return("ok")], engine="mock")
    cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
        progress_updates="none", # Even with none, typing should work?
        show_typing=True,
    )

    # We need to ensure the loop runs at least once. 4s sleep is long.
    # Mocks don't advance time for anyio.sleep.
    # Logic uses anyio.sleep(4.0).
    # To test this without waiting 4s, we'd need to mock sleep or use TestRunner with fake time?
    # runner_bridge uses `anyio.sleep`.
    # Since we can't easily mock anyio.sleep here without complex setup, 
    # we might rely on the fact that the first action is sent immediately?
    # Wait, the loop is: `while True: send; sleep`. So first one sends immediately.
    
    await handle_message(
        cfg,
        runner=runner,
        incoming=IncomingMessage(channel_id=123, message_id=10, text="hi"),
        resume_token=None,
    )

    assert transport.action_calls
    assert transport.action_calls[0]["action"] == "typing"
