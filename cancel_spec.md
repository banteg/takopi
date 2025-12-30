Below is a concrete, “drop-into-takopi” spec for a Telegram `/cancel` command that stops an in-flight `codex exec --json` **when you reply to the in-progress bot message** (or the original prompt), even if other workers are busy.

---

## `/cancel` feature spec

### Goal

Let the user stop a currently running Codex execution from Telegram by sending:

* `/cancel` **as a reply** to the bot’s “working…” progress message (preferred), **or**
* `/cancel` as a reply to the user prompt that started it.

Cancellation should:

* terminate the underlying `codex exec` subprocess,
* stop progress edits,
* produce a clear “cancelled” final state message,
* preserve a usable `resume: <uuid>` when available.

### Non-goals

* Persist cancellation capability across bot restarts (in-memory is fine).
* Cancel runs started by another takopi instance (single instance per token is already recommended).
* Perfect “instant kill” guarantees in pathological OS/process-tree cases (we’ll do best-effort + escalation).

---

## User experience

### Primary flow (reply-based cancel)

1. User sends a prompt.
2. Bot replies with a silent progress message (already exists).
3. While it’s still running, user replies to that progress message with:

```
/cancel
```

**Bot behavior:**

* Immediately updates the progress message from “working…” to **“cancelling…”**.
* Cancels the currently-active run for that session id (if any).
* Do not send an immediate ack reply; the progress edit is the immediate signal.
* After termination is confirmed, edits the progress message to a final state, e.g.:

```
cancelled · 32s · step 7

resume: `019b66fc-...`   # if known
```

* Then replies to the `/cancel` message to acknowledge termination (e.g. `🛑 Cancelled.`).

### Explicit session-id cancel

If the user sends `/cancel <uuid>` (or replies to a message containing `resume: <uuid>`), the bot cancels that session id directly and follows the same “cancelling…” → “cancelled” flow above.

### If nothing is cancellable

If the reply doesn’t map to an in-flight run (already finished, unknown message, etc.), bot replies:

`nothing is currently running for that message/session.`

### Message lifecycle on cancel

* **Do not delete** the progress message after cancellation.

  * Move it through “cancelling…” → “cancelled” and keep it as the final summary (including `resume:` when known).
  * This keeps a useful anchor message to reply to later.

---

## Internal design

### Key requirement: `/cancel` must bypass the worker queue

With the current architecture, it’s possible for all workers to be occupied (especially by many tasks waiting on the same per-session lock). If `/cancel` is just another queued “job”, it may not run until the active run finishes — which defeats cancellation.

**Spec requirement:** detect `/cancel` in `_run_main_loop()` and handle it immediately (spawn a TaskGroup task) rather than enqueueing it.

---

## Data model

Introduce an in-memory registry of active runs.

### `ActiveRunHandle`

A mutable object (or dataclass) representing one in-flight execution:

* `chat_id: int`
* `user_msg_id: int` — the user’s prompt message id
* `progress_msg_id: int | None` — the bot’s progress message id (if sent)
* `session_id: str | None` — thread id, once observed (`thread.started`)
* `exec_task: asyncio.Task[...]` — task that is actually awaiting `runner.run_serialized(...)`
* `started_at: float`
* `cancel_requested: bool`

### Indexes (in the registry)

* `by_message_id: dict[int, ActiveRunHandle]`

  * includes both `user_msg_id` and `progress_msg_id` (when known)
* `by_session_id: dict[str, ActiveRunHandle]`

  * populated once `thread.started` arrives

### Concurrency control

All registry operations should be guarded by an `asyncio.Lock` to avoid races between:

* worker tasks updating handles,
* `/cancel` tasks attempting to cancel.

---

## Cancellation mechanics

### What `/cancel` actually does

* Find the target `ActiveRunHandle`.
* Set `handle.cancel_requested = True`.
* Call `handle.exec_task.cancel()`.

This is the cleanest integration with your existing cancellation-safe subprocess code:

* `CodexExecRunner.run()` already handles `asyncio.CancelledError`
* Exiting `manage_subprocess(...)` triggers termination/kill in the context manager `finally:`

### Important change: `_handle_message` must not let CancelledError escape

In your environment, `asyncio.CancelledError` inherits `BaseException`, not `Exception`, so it will bypass existing `except Exception` blocks and could kill a worker / the TaskGroup.

**Spec requirement:** `_handle_message()` must catch `asyncio.CancelledError` when awaiting the exec task and treat it as a normal “cancelled by user” terminal outcome.

---

## Target resolution for `/cancel`

Given an incoming Telegram message `m` with text matching `/cancel`:

1. **Reply-to targeting (best):**

   * If `m.reply_to_message` exists:

     * Look up `registry.by_message_id[reply_to_message.message_id]`

       * This supports replying to:

         * the progress message id, or
         * the original user prompt id.
     * If not found, try extracting a session id from `reply_to_message.text` (final messages contain it) and fall back to session lookup.

2. **Session targeting (explicit):**

   * If `m.text` contains a `resume: <uuid>` line **or** uses `/cancel <uuid>`, cancel by session id via `by_session_id`.

3. **Ambiguous/no target:**
   * Do not guess. Reply: “reply to the in-progress message or include a resume id (`/cancel <uuid>`).”

### Idempotency

* If `handle.exec_task.done()` → respond “nothing to cancel.”
* If `handle.cancel_requested` already true → respond “already cancelling…”

---

## Required changes in code structure

### 1) `_run_main_loop` command interception

Before `queue.put(...)`, parse for `/cancel`.

**Spec behavior:**

* `/cancel` → spawn `tg.create_task(handle_cancel(...))` and `continue`
* otherwise → enqueue normal work

This ensures cancellation works even when workers are saturated.

### 2) `_handle_message` runs Codex in a child task

Instead of directly awaiting `cfg.runner.run_serialized(...)`, create an `exec_task` and register it.

Why: you need a task handle that `/cancel` can cancel without killing worker loops.

### 3) Capture `thread_id` early via `on_event`

When `on_event` sees `thread.started`:

* set `handle.session_id = evt["thread_id"]`
* add `handle` to `by_session_id`

Optional but useful:

* once `session_id` is known, include `resume: \`...`` in the progress rendering so the user can copy it mid-flight.

### 4) Render cancellation terminal state

On cancellation request:

* Immediately edit the progress message to a “cancelling…” state.
* After termination, update the progress message to show:
  * `cancelled` header (elapsed time)
  * reason line (“cancelled by user.”)
  * `resume: ...` if known
* Reply to the `/cancel` message to acknowledge termination.

---

---

## Edge cases & expected behavior

* **Cancel arrives before `thread.started`:**

  * session id may be unknown → cancellation still works via message-id mapping.
* **Multiple concurrent runs:**

  * replying to a specific progress message cancels that run only.
* **Many queued/waiting tasks for same session:**

  * `/cancel` will cancel the referenced task; (optional) you can extend to “cancel all tasks for that session” if desired.
* **Telegram edit races:**

  * once `cancel_requested` is true, `on_event` must stop scheduling any further progress edits (except the explicit “cancelling” → “cancelled” transition).

---

## Tests to add (high-value)

1. **Command parsing**

* `/cancel`, `/cancel <uuid>`

2. **Cancel updates progress message**

* Fake runner that blocks indefinitely until cancelled.
* Ensure `_handle_message` exits cleanly and edits progress to contain “cancelled”.

3. **Cancel does not kill workers**

* Ensure no `CancelledError` escapes the worker loop / TaskGroup.

4. **Cancel bypasses queue**

* Simulate all workers blocked waiting on a lock and verify a cancel task still runs and calls `.cancel()` on the active exec task.

---

## Documentation updates

* `readme.md`: add section “Cancel a Run”

  * show “reply to progress message with /cancel”
* `developing.md`: add mention of ActiveRunRegistry and why `/cancel` bypasses the queue

---

## MVP checklist (what I’d implement first)

* [ ] Add `ActiveRunRegistry` with `by_message_id`, `by_session_id`, lock
* [ ] In `_handle_message`, run `runner.run_serialized` in `exec_task`, register/unregister handle
* [ ] Catch `asyncio.CancelledError` in `_handle_message` and render “cancelled”
* [ ] Intercept `/cancel` in `_run_main_loop` and spawn `handle_cancel(...)` task (no queue)
* [ ] Add 2–4 focused tests
