Here’s what Telegram documents, what that implies for `editMessageText`, and a practical way to push update frequency **as high as possible** (under load) without living in 429-land.

## What Telegram actually rate-limits (official docs)

Telegram doesn’t publish a per-method table (“`editMessageText` is X rps”), but they *do* publish flood-control guidance for bots:

* **Per chat:** “avoid sending more than one message per second” (bursts might work briefly, but you’ll “eventually” get **429**).
* **Per group:** bots “are not be able to send more than **20 messages per minute**.”
* **Global/broadcasting:** bots can broadcast about **30 messages per second** (free tier).

And the Bot API documents how 429/backoff is communicated:

* Errors can include `parameters.retry_after` — “the number of seconds left to wait before the request can be repeated” when flood control is exceeded.

### Does `editMessageText` count?

Telegram doesn’t explicitly say “edits count as messages,” but **flood control is applied to requests**, and 429s are explicitly tied to “exceeding flood control” with `retry_after`.
In practice: **treat edits like message-send operations for limiting**. It’s the only reliable way to avoid 429s at scale.

## Can you hit 429 with multiple simultaneous runs even if each message is 2/s?

Yes — easily.

You currently do ~**2 edits/sec per progress message**.

If you run **N** concurrent agent runs, you’re roughly at **2N edits/sec** *plus* any other bot calls (send final message, sendChatAction, etc).

* At **N = 10**, that’s ~20 rps: likely OK globally.
* At **N = 20**, that’s ~40 rps: you’re above the documented ~30 rps broadcast guidance → **429 risk**.
* If several runs are in the **same chat** (or it’s a **group**), you can violate the per-chat / per-group guidance even sooner.

So: **per-message throttling alone is insufficient**; you need **global + per-chat coordination**.

## A good “max updates without 429” strategy

You want three properties:

1. **Coalesce**: don’t queue every token; keep only the latest desired render per message.
2. **Hierarchical rate limit**: enforce *both* global and per-chat budgets.
3. **429-aware backoff**: when Telegram says `retry_after`, respect it and “push out” future sends.

### Recommended limits to start with (conservative, stable)

These follow Telegram’s published guidance:

* **Per private chat:** 1 rps (1 edit/sec)
* **Per group chat:** 20/min ≈ 0.333 rps (one edit every ~3s)
* **Global:** set slightly under 30 rps (e.g. **25–28 rps**) to leave headroom for non-progress traffic and jitter.

If you *really* want to push beyond 1 rps in private chats (like your current 2/s), do it as an **optional “burst mode”** and be prepared to adapt when 429s appear. Telegram explicitly hints bursts may work temporarily.

## Architecture: central “edit pump” (best) vs wrapper limiter (minimal change)

### Option A (best under high concurrency): one shared scheduler (“edit pump”)

Instead of each `ProgressEdits.run()` directly calling Telegram, have them publish “dirty state” and let a single sender task (or small pool) send edits under one limiter.

**Pros:** best fairness, simplest to guarantee no global bursts, easiest prioritization.
**Cons:** more refactor.

### Option B (minimal change): wrap your BotClient with a shared limiter

Keep your `ProgressEdits` tasks, but every `edit_message_text` goes through a shared limiter that enforces budgets.

This usually works well if your limiter is strict and uses a lock to serialize reservations.

## Concrete limiter you can drop in (anyio-friendly)

This uses a “scheduled time” (leaky-bucket style) limiter. It’s conservative (no burst tokens), but very stable and easy to reason about.

```python
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import anyio


@dataclass
class RetryAfter(Exception):
    retry_after: float  # seconds


class TelegramRateLimiter:
    """
    Hierarchical limiter:
      - global rps cap (per bot token)
      - per-chat cap (private vs group)
      - optional 'not_before' to enforce per-message pacing too
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        global_rps: float = 28.0,          # keep headroom under ~30/s
        private_chat_rps: float = 1.0,     # 1/s guidance
        group_chat_rps: float = 20.0 / 60.0,  # 20/min guidance
    ) -> None:
        self._clock = clock
        self._sleep = sleep

        self._g_interval = 1.0 / global_rps
        self._p_interval = 1.0 / private_chat_rps
        self._gr_interval = 1.0 / group_chat_rps

        self._lock = anyio.Lock()
        self._global_next_at: float = 0.0
        self._chat_next_at: dict[int, float] = defaultdict(float)

    def _chat_interval(self, chat_id: int) -> float:
        # Heuristic: group/supergroup/channel chat IDs are negative.
        # If you want to be extra correct, you can cache getChat(chat_id).type.
        return self._gr_interval if chat_id < 0 else self._p_interval

    async def wait_turn(self, *, chat_id: int, not_before: Optional[float] = None) -> None:
        async with self._lock:
            now = self._clock()
            t = max(
                now,
                not_before or now,
                self._global_next_at,
                self._chat_next_at[chat_id],
            )
            self._global_next_at = t + self._g_interval
            self._chat_next_at[chat_id] = t + self._chat_interval(chat_id)

        await self._sleep(max(0.0, t - now))

    async def apply_retry_after(self, *, chat_id: int, retry_after: float) -> None:
        """
        Called when Telegram returns 429 + retry_after.
        We pessimistically push out both global and chat schedules.
        """
        jitter = random.uniform(0.0, min(0.25, retry_after * 0.25))
        delay = max(0.0, retry_after + jitter)

        async with self._lock:
            now = self._clock()
            until = now + delay
            self._global_next_at = max(self._global_next_at, until)
            self._chat_next_at[chat_id] = max(self._chat_next_at[chat_id], until)

        await self._sleep(delay)
```

### How to use it with your `ProgressEdits`

**Key change:** don’t rely purely on per-instance `sleep()` pacing; also acquire a **global/per-chat “turn”** before calling Telegram.

Also: update `last_edit_at` **after** the call succeeds (otherwise global waiting inside the call can make your timestamps lie).

```python
# inside ProgressEdits.__init__
def __init__(..., tg_limiter: TelegramRateLimiter, ...):
    ...
    self.tg_limiter = tg_limiter
    ...

async def run(self) -> None:
    if self.progress_id is None:
        return

    while True:
        while self.rendered_seq == self.event_seq:
            try:
                await self.signal_recv.receive()
            except anyio.EndOfStream:
                return

        # Enforce *your* per-message pacing:
        not_before = self.last_edit_at + self.progress_edit_every

        # Enforce global + per-chat pacing (shared across all runs):
        await self.tg_limiter.wait_turn(chat_id=self.chat_id, not_before=not_before)

        seq_at_render = self.event_seq
        now = self.clock()

        parts = self.renderer.render_progress_parts(now - self.started_at)
        rendered, entities = prepare_telegram(parts)

        if rendered != self.last_rendered:
            try:
                edited = await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.progress_id,
                    text=rendered,
                    entities=entities,
                )
            except RetryAfter as e:
                # Your BotClient should raise something like this on 429
                # (or you translate the HTTP response into it).
                await self.tg_limiter.apply_retry_after(
                    chat_id=self.chat_id,
                    retry_after=e.retry_after,
                )
            else:
                if edited is not None:
                    self.last_rendered = rendered
                    self.last_edit_at = now

        self.rendered_seq = seq_at_render
```

### Important: handle Bot API 429 correctly

When Telegram rate-limits you, it returns how long to wait via `retry_after`.
Your HTTP client / BotClient should surface this cleanly (either parse `parameters.retry_after` from JSON, or parse the “Too Many Requests: retry after X” description if you must).

**If you ignore `retry_after`, you’ll often get stuck in a 429 loop** (especially with many concurrent tasks).

## Make updates “as often as possible” under load (fairness + coalescing)

Even with the limiter, you should avoid building a big backlog.

### Best practice: coalesce per message (you already do)

Your `event_seq` / `rendered_seq` pattern is good: it collapses many events into one edit.

### Add prioritization (huge UX win)

When the bot is under pressure, don’t spend your limited rps budget on progress updates at the expense of:

* final answer message
* error messages
* callback query answers
* cancellation / “stopped” edits

Implementation idea:

* treat progress edits as **low-priority** requests
* treat “final response” as **high-priority**
* in your limiter/scheduler, drain high-priority first

If you do Option A (“edit pump”), this is easy with two queues.

### Add “adaptive throttling” (optional)

When concurrency spikes, even perfect rate limiting means each run updates less often. Make that predictable:

* Let `desired_interval = 0.5s` (your current)
* Let `effective_interval = max(desired_interval, N_active / global_rps)`
  (and also `>= 1s` private / `>= 3s` group)

This makes the UI degrade smoothly instead of randomly.

## Special note: groups will kill streaming

In a group, Telegram’s documented limit is **20/min per group**.
That’s ~1 update every **3 seconds** *for the entire group*.

So if you allow multiple concurrent runs in the same group chat, you basically need to:

* update **very** infrequently (e.g. 5–10s), or
* stream only in private chats, and post a final message in the group.

## A Telegram-native alternative to `editMessageText` for streaming

Bot API 9.3 added:

* `sendMessageDraft`: “stream a partial message to a user while the message is being generated” (private chats only; requires forum topic mode enabled).

If you can use it, it may be a cleaner UX than hammering edits. You’d still want a limiter, but it’s purpose-built for streaming.

## Practical checklist

1. **Implement a shared limiter** (process-wide; distributed if you run multiple instances).
2. **Enforce both**:

   * global rps (start 25–28/s)
   * per-chat rps (private 1/s, group 20/min)
3. **Always honor `retry_after`** on 429.
4. **Coalesce** updates per message (you already do) and prefer skipping intermediate renders.
5. **Prioritize** final responses over progress edits.
6. Consider **disable/slow streaming in groups**.

If you tell me:

* whether these streams are mostly in **private chats vs groups**, and
* whether you run **multiple bot processes** (or just one),
  I can suggest concrete default values (global_rps, private/group pacing, jitter) and an “edit pump” design that matches your current anyio patterns.
