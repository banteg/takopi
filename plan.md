TelegramOutbox plan (single chat, no not_before)

Goal
- Replace KeyedRateLimiter + RequestPump with a simple TelegramOutbox worker.
- Coalesce edits by message id, apply a single chat pacing interval, and honor 429 backoff via retry_at.
- Pick the next pending op by (priority, updated_at) where priority wins, then oldest.
- Priority mapping: send=0, delete=1, edit=2.

Core state
- pending_by_id: dict[message_id -> Pending(execute, priority, updated_at)]
- next_at: float (single chat pacing timestamp)
- retry_at: float (global 429 backoff)
- cond: anyio.Condition

Enqueue
- Compute message_id key (progress message id; for non-coalesced sends use a unique key).
- pending_by_id[key] = Pending(..., updated_at=now)
- cond.notify()

Special case: send that replaces progress
- If a send replaces a progress message, drop any pending edit for that message id.
- Enqueue two ops with fixed priorities: send (0) first, then delete (1).

Worker loop
1) If pending_by_id empty, wait on cond.
2) blocked_until = max(next_at, retry_at). If now < blocked_until, wait until blocked_until or notify.
3) From pending_by_id, pick the entry with smallest (priority_rank, updated_at).
4) Pop it and execute.
5) On success: next_at = now + interval (1s for private, 3s for group chosen at startup).
6) On RetryAfter(retry_after): retry_at = now + retry_after and reinsert the same Pending (keep its updated_at).

Notes
- Single chat for lifecycle, no per-chat scoping, no not_before.
- RetryAfter name preserved.
- Priority ordering is (priority, updated_at) for simple sorting.
