from __future__ import annotations

import time
from collections import defaultdict
from typing import Awaitable, Callable, Generic, Hashable, TypeVar

import anyio


class RetryAfter(Exception):
    def __init__(self, retry_after: float, description: str | None = None) -> None:
        super().__init__(description or f"retry after {retry_after}")
        self.retry_after = float(retry_after)
        self.description = description


K = TypeVar("K", bound=Hashable)


class KeyedRateLimiter(Generic[K]):
    def __init__(
        self,
        *,
        interval_for_key: Callable[[K], float],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._interval_for_key = interval_for_key
        self._clock = clock
        self._sleep = sleep
        self._lock = anyio.Lock()
        self._next_at: dict[K, float] = defaultdict(float)

    async def peek_ready_at(
        self, *, key: K | None, not_before: float | None = None
    ) -> float:
        async with self._lock:
            now = self._clock()
            target = max(now, not_before or now)
            if key is not None:
                target = max(target, self._next_at[key])
            return target

    async def commit(self, *, key: K | None, scheduled_at: float) -> float:
        async with self._lock:
            now = self._clock()
            target = max(now, scheduled_at)
            if key is not None:
                target = max(target, self._next_at[key])
                interval = self._interval_for_key(key)
                if interval > 0:
                    self._next_at[key] = target + interval
                else:
                    self._next_at[key] = max(self._next_at[key], target)
            return target

    async def apply_retry_after(self, *, key: K | None, retry_after: float) -> None:
        delay = max(0.0, retry_after)
        async with self._lock:
            now = self._clock()
            until = now + delay
            if key is not None:
                self._next_at[key] = max(self._next_at[key], until)
        await self._sleep(delay)
