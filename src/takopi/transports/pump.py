from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Generic,
    Hashable,
    Protocol,
    TypeVar,
)

import anyio

from .limiter import RetryAfter

K = TypeVar("K", bound=Hashable)

if TYPE_CHECKING:
    from anyio.abc import TaskGroup
else:
    TaskGroup = object


class PumpLimiter(Protocol[K]):
    async def peek_ready_at(
        self, *, key: K | None, not_before: float | None = None
    ) -> float: ...

    async def commit(self, *, key: K | None, scheduled_at: float) -> float: ...

    async def apply_retry_after(self, *, key: K | None, retry_after: float) -> None: ...


@dataclass(slots=True)
class PumpRequest(Generic[K]):
    execute: Callable[[], Awaitable[Any]]
    priority: int
    scope: K | None
    not_before: float | None
    coalesce_key: Hashable | None
    label: str | None = None
    done: anyio.Event = field(default_factory=anyio.Event)
    result: Any = None

    def set_result(self, result: Any) -> None:
        if self.done.is_set():
            return
        self.result = result
        self.done.set()


class RequestPump(Generic[K]):
    def __init__(
        self,
        *,
        limiter: PumpLimiter[K],
        priorities: list[int],
        clock: Callable[[], float] = time.monotonic,
        on_error: Callable[[PumpRequest[K], Exception], None] | None = None,
        on_pump_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._limiter = limiter
        self._clock = clock
        self._on_error = on_error
        self._on_pump_error = on_pump_error
        self._priority_order = list(priorities)
        self._queues: dict[int, deque[PumpRequest[K]]] = {
            priority: deque() for priority in self._priority_order
        }
        self._pending_by_key: dict[Hashable, PumpRequest[K]] = {}
        self._cond = anyio.Condition()
        self._start_lock = anyio.Lock()
        self._closed = False
        self._tg: TaskGroup | None = None

    async def _ensure_worker(self) -> None:
        async with self._start_lock:
            if self._tg is not None:
                return
            self._tg = await anyio.create_task_group().__aenter__()
            self._tg.start_soon(self._run)

    async def enqueue(self, request: PumpRequest[K], *, wait: bool = True) -> Any:
        await self._ensure_worker()
        async with self._cond:
            if self._closed:
                request.set_result(None)
                return request.result
            if request.priority not in self._queues:
                raise ValueError(f"Unknown priority: {request.priority}")
            if request.coalesce_key is not None:
                previous = self._pending_by_key.get(request.coalesce_key)
                if previous is not None:
                    previous.set_result(None)
                self._pending_by_key[request.coalesce_key] = request
            self._queues[request.priority].append(request)
            self._cond.notify()
        if not wait:
            return None
        await request.done.wait()
        return request.result

    async def drop_pending(self, *, coalesce_key: Hashable) -> None:
        async with self._cond:
            pending = self._pending_by_key.pop(coalesce_key, None)
            if pending is not None:
                pending.set_result(None)
            for queue in self._queues.values():
                if not queue:
                    continue
                kept: deque[PumpRequest] = deque()
                while queue:
                    req = queue.popleft()
                    if req.coalesce_key == coalesce_key:
                        req.set_result(None)
                        continue
                    kept.append(req)
                queue.extend(kept)
            self._cond.notify()

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._fail_pending()
            self._cond.notify_all()
        if self._tg is not None:
            await self._tg.__aexit__(None, None, None)
            self._tg = None

    def _fail_pending(self) -> None:
        for queue in self._queues.values():
            while queue:
                queue.popleft().set_result(None)
        for pending in list(self._pending_by_key.values()):
            pending.set_result(None)
        self._pending_by_key.clear()

    async def _next_ready_request(self) -> PumpRequest[K] | None:
        while True:
            request = await self._peek_request()
            if request is None:
                return None
            ready_at = await self._limiter.peek_ready_at(
                key=request.scope, not_before=request.not_before
            )
            await self._wait_until(ready_at)
            if self._clock() < ready_at:
                continue
            async with self._cond:
                if self._closed and all(not queue for queue in self._queues.values()):
                    return None
                best = self._peek_locked()
                if best is None:
                    continue
                if best is not request:
                    continue
                queue = self._queues[best.priority]
                if queue and queue[0] is best:
                    queue.popleft()
                else:
                    removed = False
                    for idx, queued in enumerate(queue):
                        if queued is best:
                            del queue[idx]
                            removed = True
                            break
                    if not removed:
                        continue
            await self._limiter.commit(key=best.scope, scheduled_at=ready_at)
            return best

    async def _peek_request(self) -> PumpRequest[K] | None:
        async with self._cond:
            while True:
                if self._closed and all(not queue for queue in self._queues.values()):
                    return None
                request = self._peek_locked()
                if request is not None:
                    return request
                await self._cond.wait()

    def _peek_locked(self) -> PumpRequest[K] | None:
        for priority in self._priority_order:
            queue = self._queues[priority]
            while queue:
                request = queue[0]
                if request.coalesce_key is not None:
                    if self._pending_by_key.get(request.coalesce_key) is not request:
                        queue.popleft()
                        continue
                return request
        return None

    async def _wait_until(self, deadline: float) -> None:
        while True:
            now = self._clock()
            delay = deadline - now
            if delay <= 0:
                return
            async with self._cond:
                with anyio.move_on_after(delay) as scope:
                    await self._cond.wait()
                if scope.cancel_called:
                    return

    async def _execute(self, request: PumpRequest[K]) -> Any:
        while True:
            try:
                return await request.execute()
            except RetryAfter as exc:
                await self._limiter.apply_retry_after(
                    key=request.scope, retry_after=exc.retry_after
                )
            except Exception as exc:
                if self._on_error is not None:
                    self._on_error(request, exc)
                return None

    async def _run(self) -> None:
        cancel_exc = anyio.get_cancelled_exc_class()
        try:
            while True:
                request = await self._next_ready_request()
                if request is None:
                    return
                if request.coalesce_key is not None:
                    async with self._cond:
                        if (
                            self._pending_by_key.get(request.coalesce_key)
                            is not request
                        ):
                            request.set_result(None)
                            continue
                result = await self._execute(request)
                request.set_result(result)
                if request.coalesce_key is not None:
                    async with self._cond:
                        if self._pending_by_key.get(request.coalesce_key) is request:
                            self._pending_by_key.pop(request.coalesce_key, None)
        except cancel_exc:
            return
        except Exception as exc:
            async with self._cond:
                self._closed = True
                self._fail_pending()
                self._cond.notify_all()
            if self._on_pump_error is not None:
                self._on_pump_error(exc)
            return
