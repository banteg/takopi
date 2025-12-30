# Progress edit throttle: leading + trailing

Status: draft

## Problem
Progress edits are currently leading-only throttled. When events arrive inside
the `progress_edit_every` window, those updates are dropped and **no trailing
update is sent**, so the UI can go stale until a later event arrives.

Example (interval = 2s):
- e1 at t=10 → edit sent
- e2 at t=11 → rate-limited, dropped
- e3 at t=60 → next edit happens here

## Goals
- Keep immediate (leading) edits when outside the throttle window.
- When rate-limited, ensure **one trailing edit** at the earliest allowed time.
- Coalesce bursts so only the latest state is sent.
- Avoid concurrent edits and stay within Telegram rate limits.

## Non-goals
- Change what progress text contains or how it’s rendered.
- Change final message behavior.
- Change CLI/config surface area beyond internal wiring.

## Proposed behavior (Option C: leading + trailing)

### Definitions
- **Interval**: `progress_edit_every` (seconds).
- **Leading edit**: immediate edit when `now - last_edit_at >= interval`.
- **Trailing edit**: deferred edit scheduled for `last_edit_at + interval` if
  events occur within the window.

### State additions in `_handle_message`
- `trailing_task: Task | None` — scheduled trailing edit worker.
- `pending_update: bool` — whether a trailing edit should fire.
- (Existing) `last_edit_at`, `edit_task`, `last_rendered`, `pending_rendered`.

### Event handling algorithm (on each event)
1. If no `progress_id` or `note_event` returns false → return.
2. Compute `now = clock()`.
3. If `edit_task` is running **or** `now - last_edit_at < interval`:
   - Set `pending_update = True`.
   - If no trailing task, schedule one for `last_edit_at + interval`.
   - Return.
4. Otherwise:
   - Cancel any existing trailing task and clear `pending_update`.
   - Render + send an edit immediately (leading).
   - Update `last_edit_at = now`.

### Trailing task algorithm
1. Sleep until `due_at = last_edit_at + interval` (no sleeps on the event path).
2. If cancelled or `pending_update` is false → exit.
3. If an edit is in flight, wait for it to complete.
4. Recompute `due_at = last_edit_at + interval`; if `now < due_at`, sleep the
   remaining time to **enforce the minimum interval**.
5. If `pending_update` is now false → exit.
6. Render the **latest** progress state using current renderer state and
   `elapsed = clock() - started_at`.
7. If rendered text differs from `last_rendered`, send the edit.
8. Update `last_edit_at` and clear `pending_update`.

### Cancellation / shutdown
- On completion, error, or cancellation: cancel `trailing_task` and await it
  (alongside `edit_task`) before sending the final message.
- If the initial progress message fails to send, do not schedule trailing edits.

## Expected behavior by example
Interval = 2s:
- e1 at t=10 → leading edit sent, `last_edit_at = 10`.
- e2 at t=11 → trailing edit scheduled for t=12.
- trailing edit fires at t=12 using latest renderer state.
- e3 at t=60 → leading edit sent immediately.

If additional events arrive before t=12, they only update renderer state; the
single trailing edit at t=12 reflects the latest state.

## Testing plan
- Update `tests/test_exec_bridge.py`:
  - `test_progress_edits_are_rate_limited` should now expect a trailing edit.
  - Add a test for coalescing: multiple events within the window result in
    exactly one trailing edit with the latest state.
  - Add a test that a leading edit cancels a pending trailing task.
- Consider injecting a `sleep` function into `_handle_message` (defaulting to
  `asyncio.sleep`) to make trailing behavior deterministic and fast in tests.

## Notes / tradeoffs
- Trailing edits add a bit of latency but guarantee eventual freshness.
- Scheduling work is isolated in a task to avoid blocking the Codex event stream.
- To keep the handler readable, encapsulate progress-edit state and logic in a
  small helper/controller (avoid large `nonlocal` blocks).
