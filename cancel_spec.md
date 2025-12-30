# `/cancel` feature spec

## Goal

Let the user stop a running Codex execution from Telegram by replying to the bot's progress message with `/cancel`.

Cancellation:

- terminates the underlying `codex exec` subprocess
- stops progress edits
- produces a "cancelled" final state in the progress message
- preserves the `resume:` tag when available

## Non-goals

- Persist cancellation capability across bot restarts (in-memory is fine)
- Cancel runs started by another takopi instance
- Rollback file changes made by Codex (Codex's resume mechanism handles partial state)
- Cancel queued/waiting tasks for the same session (cancel only affects the active run)

---

## User experience

### Primary flow (reply-based cancel)

1. User sends a prompt
2. Bot replies with a silent progress message (already exists)
3. While running, user replies to that progress message with:

```
/cancel
```

**Bot behavior:**

- Immediately cancels the run
- Edits the progress message to show "cancelled" state (matching existing terminal state styling)
- Includes elapsed time, step info, and `resume:` tag

**Example final state:**

```
cancelled · 32s · step 7

resume: `019b66fc-...`
```

### Cancel before session starts

If `/cancel` is sent before the `thread.started` event (resume tag not yet visible in progress message), the bot replies:

`nothing is currently running for that message.`

The user can retry once the resume tag appears.

### Bare /cancel (no reply)

If `/cancel` is sent without replying to a message, bot replies:

`reply to the progress message to cancel.`

### Already finished

If `/cancel` targets a completed run (session_id not in running tasks), bot replies:

`nothing is currently running for that message.`

### Duplicate cancels

Each `/cancel` gets a response, even duplicates. After a run is cancelled, subsequent `/cancel` attempts get "nothing running" response.

---

## Internal design

### Queue bypass

`/cancel` must bypass the worker queue. If all workers are blocked (e.g., waiting on per-session lock), `/cancel` would never execute if queued normally.

**Requirement:** Detect `/cancel` in `_run_main_loop()` before enqueuing and spawn a TaskGroup task to handle it immediately.

### Running tasks tracking

Add `self.running_tasks: dict[str, asyncio.Task]` to the Bot class, mapping session_id → asyncio.Task.

- **Register:** When `thread.started` event provides the session_id, add to dict
- **Unregister:** When task completes (success, error, or cancelled), remove from dict
- **No lock needed:** Single-threaded asyncio, dict operations between awaits are atomic

### Target resolution

Given a `/cancel` reply:

1. Get the `reply_to_message` text
2. Parse the `resume:` tag (support both `resume: uuid` and `resume: \`uuid\``)
3. Look up `session_id` in `self.running_tasks`
4. If found and task not done: cancel it
5. If not found or done: reply "nothing running"

### Cancellation mechanics

```python
task = self.running_tasks.get(session_id)
if task and not task.done():
    task.cancel()
```

The `asyncio.CancelledError` propagates through the await chain. The existing `manage_subprocess` context manager handles subprocess termination in its `finally:` block.

**Subprocess termination:** Sending SIGTERM to Codex triggers its internal handling, which sends SIGKILL to the entire process group. Termination is near-instant.

### CancelledError handling

`_handle_message` must catch `asyncio.CancelledError` and render the "cancelled" state instead of letting it propagate and kill the worker.

```python
try:
    result = await exec_task
except asyncio.CancelledError:
    # Render cancelled state to progress message
    ...
```

### Command detection

Match exact `/cancel` (case-sensitive). No bot suffix, no UUID argument, no variations.

---

## Summary of simplifications

Compared to the original spec:

| Original | Simplified |
|----------|------------|
| Two indexes (by_message_id, by_session_id) | Single dict by session_id only |
| ActiveRunHandle dataclass with multiple fields | Just `dict[str, asyncio.Task]` |
| asyncio.Lock for registry | No lock (single-threaded asyncio) |
| "cancelling..." intermediate state | Skip straight to "cancelled" |
| Reply to /cancel message with ack | Edit progress message only |
| `/cancel <uuid>` explicit form | Reply-based only |
| `cancel_requested` flag | Not needed, CancelledError is sufficient |
| Separate ActiveRunRegistry class | Simple dict on Bot instance |
| TTL-based retention | Immediate removal |
| Message-id based early cancel | Wait for resume tag (accept the gap) |

---

## Required code changes

### 1. Add running_tasks dict to Bot

```python
self.running_tasks: dict[str, asyncio.Task] = {}
```

### 2. Intercept /cancel in _run_main_loop

Before `queue.put(...)`:

```python
if text == "/cancel":
    tg.create_task(self.handle_cancel(message))
    continue
```

### 3. Track exec_task by session_id

When `thread.started` event arrives:

```python
self.running_tasks[session_id] = exec_task
```

When task completes:

```python
self.running_tasks.pop(session_id, None)
```

### 4. Catch CancelledError in _handle_message

Render cancelled state instead of propagating.

### 5. Implement handle_cancel

- Parse reply_to_message for session_id
- Look up in running_tasks
- Call task.cancel()
- Handle error cases with appropriate replies

---

## Edge cases

| Case | Behavior |
|------|----------|
| Cancel before thread.started | "nothing running" (resume tag not yet visible) |
| Cancel after completion | "nothing running" |
| Bare /cancel (no reply) | "reply to the progress message to cancel" |
| Multiple concurrent runs | Reply targets specific run via its progress message |
| Duplicate /cancel spam | Each gets "nothing running" after first succeeds |

---

## Tests

**Minimum (unit tests):**

- Command parsing (exact `/cancel` match)
- Session_id extraction from message text (both formats)
- Running tasks dict operations

**Stretch (integration):**

- Full cancel flow with mocked Telegram client
- Verify CancelledError doesn't escape worker loop
- Verify subprocess termination path

---

## Documentation

Add `/cancel` to existing commands section in readme (not a dedicated section).

---

## Implementation checklist

- [ ] Add `running_tasks` dict to Bot
- [ ] Intercept `/cancel` in `_run_main_loop`, spawn task (bypass queue)
- [ ] Track session_id → Task when `thread.started` arrives
- [ ] Catch `CancelledError` in `_handle_message`, render cancelled state
- [ ] Implement `handle_cancel` with reply parsing and error handling
- [ ] Verify subprocess termination path works via CancelledError propagation
- [ ] Check for existing logging/metrics hooks that need cancel awareness
- [ ] Add unit tests
- [ ] Update readme
