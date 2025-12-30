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

### State additions in `handle_message`
- `publisher_task: Task | None` — background progress publisher worker.
- `event_seq: int` — monotonically increasing event counter (progress state changed).
- `published_seq: int` — last `event_seq` that has been published.
- `wakeup: Event` — wakes the publisher when new events arrive.
- (Existing) `last_edit_at`, `last_rendered`.

### Event handling algorithm (on each event)
1. If no `progress_id` → return.
2. If `note_event` returns false → return.
3. Increment `event_seq` and `wakeup.set()` (no sleeps / edits on the event path).

### Publisher task algorithm
1. Wait for `wakeup`.
2. While `published_seq < event_seq`:
   - Sleep until `last_edit_at + interval` to **enforce the minimum interval**.
   - Render the **latest** progress state (coalesces bursts).
   - If rendered text differs from `last_rendered`, send the edit and update
     `last_edit_at` and `last_rendered`.
   - Set `published_seq` to the latest `event_seq` observed before rendering.
3. Loop back to waiting for `wakeup`.

### Cancellation / shutdown
- On completion, error, or cancellation: cancel `publisher_task` and await it
  before sending the final message.
- If the initial progress message fails to send, do not start the publisher task.

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
- Consider injecting a `sleep` function into `handle_message` (defaulting to
  `asyncio.sleep`) to make behavior deterministic and fast in tests.

## Notes / tradeoffs
- Trailing edits add a bit of latency but guarantee eventual freshness.
- Scheduling work is isolated in a task to avoid blocking the Codex event stream.
- To keep the handler readable, encapsulate progress-edit state and logic in a
  small helper/controller (avoid large `nonlocal` blocks).
