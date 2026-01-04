Below is a concrete spec for the refactor:

* **Introduce `Transport` + `Presenter` protocols** (like `Runner`: small, stable boundary)
* **Move Telegram-specific loop/parsing into `takopi/bridges/telegram.py`**
* Keep the current behavior (progress message + edits + final message, cancel via reply) with minimal “new features”

This follows the “make Telegram an adapter, not the core” direction recommended in the reviews.  

---

# Spec: Transport + Presenter protocols and Telegram bridge split

## Background

Today `src/takopi/bridge.py` mixes:

1. Orchestration (runner execution, progress edit throttling, cancellation)
2. Presentation (MarkdownParts → Telegram entities via `prepare_telegram`)
3. Transport (Telegram API via `BotClient`)
4. Inbox loop (`getUpdates`) and Telegram-specific message semantics (`/cancel`, `/codex`, reply-to-resume)

That’s a solid monolith, but it blocks using Takopi as a library building block because “bridge = Telegram”. Multiple reviews suggested abstracting transport and presentation to support other frontends without expanding the feature set.  

---

## Goals

### Primary goals

* Extract a **small, stable output boundary**:

  * `Transport`: how to send/edit/delete a message
  * `Presenter`: how to turn Takopi’s rendered output into a `Transport` payload
* Make a **Telegram bridge module** that:

  * does `getUpdates` polling
  * handles `/cancel` and engine override commands
  * uses the core orchestrator to run runners and stream progress

### Secondary goals

* Make testing easier: orchestration tests shouldn’t need a Telegram-shaped fake bot.

### Non-goals (explicitly out of scope)

* Implement Slack/Discord/Web transport now.
* Change runner translation/event model.
* Add new user-facing features or configuration knobs.

---

## Package layout

### New modules

```
src/takopi/transport.py            # Transport protocol + message types
src/takopi/presenter.py            # Presenter protocol
src/takopi/bridges/__init__.py
src/takopi/bridges/telegram.py     # Telegram-only loop + wiring
src/takopi/exec_bridge.py          # Transport-agnostic orchestration core
```

### Existing modules (unchanged or minimally touched)

* `src/takopi/render.py` stays as the pure-ish renderer that outputs `MarkdownParts` and provides `prepare_telegram` (Telegram presenter can wrap it).

### Backward compatibility

* Keep `src/takopi/bridge.py` as a **compat shim** for one release cycle:

  * Re-export `run_main_loop` and `BridgeConfig` (Telegram) from `takopi.bridges.telegram`
  * Re-export (or alias) `handle_message` from `takopi.exec_bridge` if needed
* Update internal imports (`cli.py`, tests) to new locations immediately.

(If you’re comfortable with a breaking change in 0.6.0, you can skip the shim and update call sites.)

---

## Core types and protocols

## `transport.py`

### Type aliases

```py
from typing import Any, Protocol, TypeAlias
from dataclasses import dataclass, field

ChannelId: TypeAlias = int | str
MessageId: TypeAlias = int | str
```

### Message reference

```py
@dataclass(frozen=True, slots=True)
class MessageRef:
    channel_id: ChannelId
    message_id: MessageId
    raw: Any | None = None  # optional transport-native response
```

### Rendered payload (output of Presenter)

Keep this intentionally small and generic to avoid feature bloat:

```py
@dataclass(frozen=True, slots=True)
class RenderedMessage:
    text: str
    extra: dict[str, Any] = field(default_factory=dict)
    # Telegram: {"entities": [...]} or {"parse_mode": "HTML"}
    # Others can store their own payload shapes in extra.
```

### Send options

```py
@dataclass(frozen=True, slots=True)
class SendOptions:
    reply_to: MessageRef | None = None
    notify: bool = True  # transport maps to disable_notification where relevant
```

### Transport protocol

```py
class Transport(Protocol):
    async def close(self) -> None: ...

    async def send(
        self,
        *,
        channel_id: ChannelId,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None: ...

    async def edit(
        self,
        *,
        ref: MessageRef,
        message: RenderedMessage,
    ) -> MessageRef | None: ...
    # returns None if edit unsupported or failed

    async def delete(self, *, ref: MessageRef) -> bool: ...
```

**Design choice:** edits/deletes are “best effort”; core already treats Telegram failures as non-fatal.

---

## `presenter.py`

### Presenter protocol

```py
from typing import Protocol
from .render import MarkdownParts
from .transport import RenderedMessage

class Presenter(Protocol):
    def render(self, parts: MarkdownParts) -> RenderedMessage: ...
```

**Why `MarkdownParts` instead of `TakopiEvent`:** you already centralize progress/final formatting in `ExecProgressRenderer`. Keep that separation.

---

## Core orchestration (`exec_bridge.py`)

### Incoming message type (minimal)

Core should not parse Telegram update dicts. Instead it receives a normalized incoming message:

```py
from dataclasses import dataclass
from .transport import ChannelId, MessageId, MessageRef

@dataclass(frozen=True, slots=True)
class IncomingMessage:
    channel_id: ChannelId
    message_id: MessageId
    text: str
    reply_to: MessageRef | None = None
```

### Config

```py
from dataclasses import dataclass
from .transport import Transport
from .presenter import Presenter

PROGRESS_EDIT_EVERY_S = 2.0

@dataclass(frozen=True)
class ExecBridgeConfig:
    transport: Transport
    presenter: Presenter
    final_notify: bool
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S
```

### RunningTask stays (but with `MessageRef` keys)

If you want to preserve the Telegram behavior where `/cancel` targets a progress message, the “running task registry” remains, but it should be keyed by `MessageRef` (not `int`) for generality:

```py
@dataclass
class RunningTask:
    resume: ResumeToken | None = None
    resume_ready: anyio.Event = field(default_factory=anyio.Event)
    cancel_requested: anyio.Event = field(default_factory=anyio.Event)
    done: anyio.Event = field(default_factory=anyio.Event)

RunningTasks = dict[MessageRef, RunningTask]
```

### Replace BotClient calls with Transport+Presenter

#### `_send_or_edit_markdown` becomes `_send_or_edit_parts`

```py
async def _send_or_edit_parts(
    transport: Transport,
    presenter: Presenter,
    *,
    channel_id: ChannelId,
    parts: MarkdownParts,
    edit_ref: MessageRef | None = None,
    reply_to: MessageRef | None = None,
    notify: bool = True,
    prepared: RenderedMessage | None = None,
) -> tuple[MessageRef | None, bool]:
    msg = prepared or presenter.render(parts)
    if edit_ref is not None:
        edited = await transport.edit(ref=edit_ref, message=msg)
        if edited is not None:
            return edited, True
    sent = await transport.send(
        channel_id=channel_id,
        message=msg,
        options=SendOptions(reply_to=reply_to, notify=notify),
    )
    return sent, False
```

#### `ProgressEdits` edits via transport

* Store `last_rendered: RenderedMessage | None` (not `str`)
* Compare `RenderedMessage` objects for equality (covers `text` + `extra`)

### `send_initial_progress` changes

* returns `ProgressMessageState` with a `MessageRef | None` for `progress_ref`

```py
@dataclass(frozen=True, slots=True)
class ProgressMessageState:
    ref: MessageRef | None
    last_edit_at: float
    last_rendered: RenderedMessage | None
```

* uses `transport.send(... notify=False, reply_to=incoming message)`

### `handle_message` becomes transport-agnostic

Signature becomes:

```py
async def handle_message(
    cfg: ExecBridgeConfig,
    *,
    runner: Runner,
    incoming: IncomingMessage,
    resume_token: ResumeToken | None,
    strip_resume_line: Callable[[str], bool] | None = None,
    running_tasks: RunningTasks | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> None:
    ...
```

Behavior stays the same:

* Send progress reply silently.
* Start edits task if we have a progress message reference.
* Run runner; stream events into renderer.
* On completion:

  * if `cfg.final_notify`: send final reply (notify=True) and delete progress
  * else: edit progress into final state
* On error/cancel: edit progress message into error/cancel state.

**Key point:** `incoming.channel_id` replaces `chat_id`, and `incoming.reply_to` replaces Telegram reply dicts.

---

## Telegram bridge (`bridges/telegram.py`)

Telegram module owns:

* polling updates
* filtering by configured `chat_id`
* `/cancel` semantics (reply to progress message)
* engine override parsing (leading `/codex …`)
* resume-token extraction using router + reply-to text
* scheduler usage (`ThreadScheduler`) to serialize by thread

This matches the intent of “Telegram is an adapter”. 

### TelegramPresenter

In `bridges/telegram.py` (or `presenters/telegram.py` later):

```py
class TelegramPresenter:
    def render(self, parts: MarkdownParts) -> RenderedMessage:
        text, entities = prepare_telegram(parts)
        return RenderedMessage(text=text, extra={"entities": entities})
```

### TelegramTransport

Wrap existing `BotClient`:

```py
class TelegramTransport:
    def __init__(self, bot: BotClient) -> None:
        self._bot = bot

    async def close(self) -> None:
        await self._bot.close()

    async def send(...):
        # map options.reply_to.message_id -> reply_to_message_id
        # map options.notify -> disable_notification
        # pass message.extra through (entities/parse_mode)
        ...

    async def edit(...):
        ...

    async def delete(...):
        ...
```

### Telegram bridge config

Keep Telegram-only settings separate from core config:

```py
@dataclass(frozen=True)
class TelegramBridgeConfig:
    bot: BotClient
    router: AutoRouter
    chat_id: int
    startup_msg: str
    exec_cfg: ExecBridgeConfig
```

Construction in CLI becomes:

* `bot = TelegramClient(token)`
* `transport = TelegramTransport(bot)`
* `presenter = TelegramPresenter()`
* `exec_cfg = ExecBridgeConfig(transport=transport, presenter=presenter, final_notify=..., progress_edit_every=...)`
* `TelegramBridgeConfig(..., exec_cfg=exec_cfg)`

### `poll_updates` stays Telegram-shaped (minimal change)

It can continue yielding raw Telegram messages (`dict[str, Any]`) to keep diff small, but `run_main_loop` should convert to `IncomingMessage` before calling `exec_bridge.handle_message`.

### `run_main_loop` wiring

Flow remains identical to today:

* `_set_command_menu(cfg.bot, cfg.router)` (still Telegram-only)
* `scheduler = ThreadScheduler(...)`
* `running_tasks: dict[MessageRef, RunningTask] = {}`

When a message comes in:

1. If `/cancel`:

   * require reply-to message
   * look up `running_tasks[MessageRef(chat_id, reply_to_message_id)]`
   * set cancel flag
2. Else:

   * parse engine override command from text (`/codex ...`)
   * `resume_token = router.resolve_resume(text, reply_to_text)`
   * If replying to an in-flight progress message and no resume token found:

     * wait for running_task.resume_ready, then enqueue resume
3. Finally call core:

   * `incoming = IncomingMessage(channel_id=chat_id, message_id=user_msg_id, text=text, reply_to=MessageRef(chat_id, reply_id) if reply_id else None)`
   * `await exec_bridge.handle_message(cfg.exec_cfg, runner=..., incoming=incoming, resume_token=resume_token, strip_resume_line=cfg.router.is_resume_line, running_tasks=running_tasks, on_thread_known=scheduler.note_thread_known, ...)`

### Telegram-only helper moves

Move these out of core and keep in `bridges/telegram.py`:

* `_is_cancel_command`
* `_strip_engine_command`
* `_build_bot_commands`
* `_set_command_menu`
* `_drain_backlog` / `_send_startup`
* `_send_with_resume` and `_handle_cancel`

The reviews pointed out “bridge is too Telegram-centric”; this split is the fix without adding features.  

---

## Backward compatibility plan

### Option 1: Soft landing (recommended)

* Keep `takopi/bridge.py` but reduce it to:

  * `from .bridges.telegram import TelegramBridgeConfig as BridgeConfig, run_main_loop, poll_updates, ...`
  * plus `DeprecationWarning` in module import or docstrings
* Update `cli.py` and tests to import from `takopi.bridges.telegram` directly.

### Option 2: Clean break (v0.6.0)

* Remove `takopi.bridge` Telegram symbols entirely.
* Document import changes in changelog.

---

## Testing plan

### Core orchestration tests

Replace `_FakeBot` with:

* `_FakeTransport` capturing:

  * `send_calls: list[(channel_id, RenderedMessage, SendOptions)]`
  * `edit_calls: list[(MessageRef, RenderedMessage)]`
  * `delete_calls: list[MessageRef]`
* `_PlainPresenter` rendering MarkdownParts via `assemble_markdown_parts(parts)` into `RenderedMessage(text=...)`

Then port existing `test_exec_bridge.py` tests that care about orchestration:

* final notify behavior
* rate-limited progress edits (fake clock)
* resume line stripping
* cancelled state rendering
* error state preserves resume token

Those tests become transport-agnostic and faster.

### Telegram bridge tests

Keep a thinner set:

* `/cancel` parsing and routing
* reply-to-running-progress behavior (`_send_with_resume` path)
* `_strip_engine_command` and `_build_bot_commands`
* You can keep `poller` tests largely the same but make them yield Telegram dicts and check `FakeTransport` outputs.

### Telegram client tests

`test_telegram_client.py` stays unchanged (still tests HTTP behavior of TelegramClient).

---

## Implementation checklist (suggested PR breakdown)

### PR 1: Introduce protocols and core module (no behavior change)

* Add `transport.py`, `presenter.py`
* Add `exec_bridge.py` (copy from current bridge, replace BotClient calls with Transport/Presenter)
* Add `TelegramTransport` + `TelegramPresenter` (temporary location ok)

### PR 2: Move Telegram loop to `bridges/telegram.py`

* Create `bridges/telegram.py`
* Move poll loop + cancel logic + router wiring there
* Keep CLI working (update imports)
* Add `bridges/__init__.py`

### PR 3: Test migration

* Refactor `test_exec_bridge.py` to test `exec_bridge` with fake transport/presenter
* Add a smaller test file for telegram bridge behaviors

### PR 4: Compatibility + docs

* Either add shim `takopi/bridge.py` or do the breaking change
* Update README + changelog

---

## Open decisions (call out now, implement later)

1. **What if `Transport.edit()` returns None?**
   Proposed: for progress updates, silently stop editing (no fallback sends). For final message, fall back to `send()` if edit fails.

2. **What does “reply_to” mean in non-Telegram transports?**
   It’s optional; transports can ignore it.

3. **Do we want a separate `Inbox` protocol?**
   Not for this change. Telegram loop stays in `bridges/telegram.py`. If you later want a generic event loop, introduce `Inbox` separately.

---

If you want, I can also sketch the minimal diffs for:

* `cli.py` (*parse_bridge_config wiring to new Telegram config*)
* `tests/test_exec_bridge.py` (*FakeBot → FakeTransport + PlainPresenter*)
