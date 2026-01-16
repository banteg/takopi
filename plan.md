Here are the spots that most “smell” to me in this repo, plus concrete refactor/simplification ideas you can apply without changing behavior. I’m focusing on code that is (a) large, (b) doing multiple jobs, (c) duplicated, or (d) hard to test/modify safely.

---

## 1) `src/takopi/telegram/loop.py:467–1512` — `run_main_loop` is doing *everything*

### Why it smells

`run_main_loop` is ~1,046 lines and is effectively a whole subsystem implemented as one function with a ton of nested closures and shared mutable state:

* bootstrapping stores (prefs/sessions/topics)
* config watching + reload behavior
* scheduler wiring
* forward-coalescing (“debounce” prompt execution)
* media group buffering
* command parsing + dispatch (builtins + plugin commands)
* voice transcription
* file upload behavior
* resume token resolution logic (reply-based + topic/chat session based)
* trigger mode (mentions/all) gating

The nested helpers inside `run_main_loop` are a classic sign that the function is actually a “class with hidden fields” (captured outer variables), which makes it harder to:

* unit test individual behaviors,
* reason about what state is mutated where,
* add a new feature without touching unrelated logic.

### Refactor direction that stays safe

You have a big test suite for `run_main_loop` (lots in `tests/test_telegram_bridge.py`), which is great: you can refactor aggressively while leaning on it.

#### A. Turn the closure soup into an explicit state object

Right now, `run_main_loop` has “fields” like:

* `running_tasks`, `pending_prompts`, `media_groups`
* `topic_store`, `chat_session_store`, `chat_prefs`
* `resolved_topics_scope`, `topics_chat_ids`, `bot_username`
* config knobs (`forward_coalesce_s`, `media_group_debounce_s`)

Make them explicit:

```py
@dataclass(slots=True)
class TelegramLoopState:
    running_tasks: RunningTasks
    pending_prompts: dict[ForwardKey, _PendingPrompt]
    media_groups: dict[tuple[int, str], _MediaGroupState]
    topic_store: TopicStateStore | None
    chat_session_store: ChatSessionStore | None
    chat_prefs: ChatPrefsStore | None
    resolved_topics_scope: str | None
    topics_chat_ids: frozenset[int]
    bot_username: str | None
    command_ids: set[str]
    reserved_commands: set[str]
    reserved_chat_commands: set[str]
    forward_coalesce_s: float
    media_group_debounce_s: float
```

Then you can extract helper functions that accept `(cfg, state, tg, scheduler, msg)` rather than capturing a bunch of outer locals.

#### B. Split message handling into a “router” function with a small context struct

Inside the big `async for msg in poller(cfg)` loop, you recompute a lot of derived context (topic key, session key, bound context, ambient context, etc.). Create a per-message context object once:

```py
@dataclass(frozen=True, slots=True)
class TelegramMsgContext:
    chat_id: int
    thread_id: int | None
    reply_id: int | None
    reply_ref: MessageRef | None
    topic_key: tuple[int, int] | None
    chat_session_key: tuple[int, int | None] | None
    stateful_mode: bool
    chat_project: str | None
    ambient_context: RunContext | None
```

Then the loop becomes something like:

```py
async for update in poller(cfg):
    match update:
        case TelegramCallbackQuery() -> handle_callback(...)
        case TelegramIncomingMessage() -> ctx = await build_ctx(...); await route_message(ctx, ...)
```

That alone can shave hundreds of lines off `run_main_loop`.

#### C. Extract the “resume token resolution” logic (it’s duplicated)

You currently have very similar code in:

* `run_prompt_from_upload(...)`
* `_dispatch_pending_prompt(...)`

Both do:

1. parse directives / context
2. determine engine defaults
3. try reply-to-running-task resume
4. try topic session resume
5. try chat session resume
6. either start a run or enqueue resume (with queued-progress message)

That should be a single function, because any future tweaks to resume behavior will otherwise have to be made in 2–3 places and will drift.

Make one helper:

```py
async def resolve_resume_plan(...) -> ResumePlan:
    # returns: engine_override, resume_token, should_resume_via_running_task, etc.
```

Even if `ResumePlan` is just a tiny dataclass, it’s worth it.

#### D. Pull forward-coalescing and media-group buffering into their own components

These two are *state machines* embedded inside the main loop:

* Forward coalescing: `_schedule_prompt`, `_attach_forward`, `_debounce_prompt_run`, `_cancel_pending_prompt`
* Media groups: `flush_media_group` + `media_groups` dict

These are perfect candidates for small classes with clear APIs:

```py
class ForwardCoalescer:
    def __init__(..., debounce_s: float, tg: TaskGroup): ...
    def on_prompt(self, pending: _PendingPrompt) -> None: ...
    def on_forward(self, msg: TelegramIncomingMessage) -> None: ...
    def cancel(self, key: ForwardKey) -> None: ...
```

```py
class MediaGroupBuffer:
    def on_message(self, msg) -> bool:  # returns “consumed”
```

This reduces the cognitive load in the message router and makes each behavior testable independently.

#### E. Stop importing “private” command handlers into the loop

`telegram/loop.py` imports a lot of underscore-prefixed functions like `_handle_model_command`, `_handle_reasoning_command`, `_handle_trigger_command`, etc.

That’s a minor smell, but it usually indicates the public API boundaries aren’t clear. You can fix this in a very low-risk way:

* Rename `_handle_model_command` → `handle_model_command`
* Export it from `telegram/commands/__init__.py`
* Update loop to import from the package rather than module-privates

Even if you *keep* the underscore names, consider a “commands facade” module that reexports all handlers used by the loop.

---

## 2) `src/takopi/telegram/commands/model.py` vs `reasoning.py` (and `trigger.py`) — heavy copy/paste

### Why it smells

`model.py` and `reasoning.py` are almost the same file (diff is mostly renaming “model” ↔ “reasoning” plus the allowed-level validation). `trigger.py` follows the same structural pattern (permission check + topic/chat override + show/set/clear).

This kind of duplication is exactly where bugs appear later (“we fixed it in reasoning, forgot in model”) and it increases friction for adding new override knobs.

### Simplification/refactor

Make a shared helper module for the common patterns, and keep each command file focused on the “field-specific” parts.

#### A. Factor out the common admin/group permission check

You have the same logic 3 times with different strings:

* `_check_model_permissions`
* `_check_reasoning_permissions`
* `_check_trigger_permissions`

Make one:

```py
async def require_admin_or_private(
    cfg, msg, *, purpose: str, failure_prefix: str
) -> bool:
    ...
```

Where `purpose` becomes “model override”, “reasoning override”, etc.

#### B. Factor out engine selection from reply context

`_resolve_engine_selection(...)` is duplicated (model + reasoning). Put it in `telegram/commands/engine_selection.py` or similar.

#### C. Factor out “topic vs chat scope update” into a helper

Model and reasoning both do:

* If `tkey is not None`: operate on `topic_store`
* Else: operate on `chat_prefs`

That’s essentially:

```py
async def update_override(
    *,
    tkey: tuple[int,int] | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    chat_id: int,
    engine: str,
    mutate: Callable[[EngineOverrides | None], EngineOverrides],
) -> Literal["topic","chat"]:
    ...
```

Then “set model” becomes a tiny field-specific mutation function.

#### D. Put the shared label maps in one place

Both commands define the same `source_labels` and `override_labels`. Move them to constants (or a helper function).

Result: `model.py` and `reasoning.py` will likely drop to ~1/3 of their current size.

---

## 3) `src/takopi/runner.py:343–596` — `JsonlSubprocessRunner.run_impl` mixes too many concerns

### Why it smells

`run_impl` currently does (at once):

* build command / env / payload
* spawn subprocess
* concurrently drain stderr
* read JSONL stdout
* parse JSON (with different failure modes)
* translate decoded events (also with failure modes)
* enforce resume token semantics (`StartedEvent` expected vs found)
* enforce “first CompletedEvent wins; ignore after”
* decide which fallback completion to emit if subprocess ends weirdly

This is all correct logic, but it’s packed into one large loop with many flags (`did_emit_completed`, `ignored_after_completed`, `found_session`, etc.). That’s a maintenance hazard.

### Suggested refactor (incremental, test-friendly)

Keep behavior exactly the same, but extract the sub-steps into small methods so a reader can understand it top-down.

A good decomposition:

1. `async def _spawn_process(...) -> proc`
2. `async def _send_stdin(proc, payload)`
3. `async def _iter_decoded_lines(proc.stdout) -> AsyncIterator[DecodedLineResult]`

   * one place that turns bytes → either `{ok: True, data}` or `{ok: False, error_kind, raw_text, line_text}`
4. `def _translate_or_note(decoded) -> list[TakopiEvent]`
5. `def _handle_started_invariants(...) -> (found_session, emit)`
6. `async def _emit_events_until_completed(...) -> (did_emit_completed, found_session)`

This leaves `run_impl` as a readable orchestration layer.

Bonus simplification: you already have `EventFactory` used by some runners; you could consider using an `EventFactory` inside `JsonlSubprocessRunner` too (for consistent “resume token tracking”), but that’s optional.

---

## 4) `telegram/loop.py` — `_dispatch_builtin_command` is doing extra work per message

### Why it smells

`_dispatch_builtin_command` (around lines 136–256) builds a dict every time, even though the command set is small and mostly static. Also: most handlers are imported as private underscored functions, which weakens module boundaries.

### Simplifications

* Replace “build dict then look up” with a simple `match command_id:` with early returns.
* Or build the handler mapping once when the loop starts (and rebuild it only on reload, if needed).

This is not your biggest problem, but it’s cheap to clean up and reduces churn inside the hot path.

---

## 5) `src/takopi/cli.py` — monolith CLI module (less urgent, but trending smelly)

### Why it smells

`cli.py` is ~795 LOC and contains:

* setup orchestration (`_run_auto_router`)
* onboarding integration
* project init logic
* doctor checks
* plugin introspection
* transport selection, locking

This is typical for Typer apps, but it will keep growing.

### Refactor direction

Use Typer’s “sub-app per module” structure:

* `cli/main.py` wires the root `Typer()`
* `cli/run.py` for run/autorouter
* `cli/init.py` for project registration
* `cli/doctor.py` for diagnostics
* `cli/plugins.py` for plugin listing

This makes it easier to navigate and reduces the “scroll fatigue”.

---

## 6) Small but valuable simplifications

### A. Add convenience properties to Telegram message types

A lot of code repeats “is private?” logic:

```py
is_private = msg.chat_type == "private"
if msg.chat_type is None:
    is_private = msg.chat_id > 0
```

Add:

```py
@dataclass(frozen=True, slots=True)
class TelegramIncomingMessage:
    ...
    @property
    def is_private(self) -> bool:
        if self.chat_type is not None:
            return self.chat_type == "private"
        return self.chat_id > 0
```

Then permission checks in `/model`, `/reasoning`, `/trigger` get simpler *and consistent*.

### B. Reduce huge parameter lists by bundling “context”

You have several functions taking 8–12 parameters (e.g., `_dispatch_builtin_command`, `_run_engine` callers, etc.). Introduce a `TelegramCommandContext` / `TelegramLoopDeps` dataclass so signatures don’t balloon.

### C. Normalize “topic_key resolution” into one function

The code repeatedly does:

```py
topic_key = _topic_key(msg, cfg, scope_chat_ids=topics_chat_ids) if topic_store else None
```

That can live in `build_ctx(...)` and be passed around.

---

## A pragmatic “do this first” order

If you want the biggest payoff with the least risk:

1. **Extract forward-coalescing + media-group buffering** into small components (they’re self-contained).
2. **Extract resume token resolution** into one helper and use it from both prompt paths.
3. **Create `TelegramMsgContext`** to centralize derived message state.
4. **Refactor override commands** (`model` + `reasoning` + `trigger`) into shared helpers.
5. **Break `JsonlSubprocessRunner.run_impl`** into smaller methods (no behavior changes).

That sequence cuts the worst complexity first and makes the next refactor steps easier.

