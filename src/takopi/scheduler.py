from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import anyio

from .model import ResumeToken


@dataclass(frozen=True, slots=True)
class ThreadJob:
    chat_id: int
    user_msg_id: int
    text: str
    resume_token: ResumeToken


RunJob = Callable[[ThreadJob], Awaitable[None]]


class TaskGroup(Protocol):
    def start_soon(
        self, func: Callable[..., Awaitable[object]], *args: Any
    ) -> None: ...


class ThreadScheduler:
    def __init__(self, *, task_group: TaskGroup, run_job: RunJob) -> None:
        self._task_group = task_group
        self._run_job = run_job
        self._lock = anyio.Lock()
        self._pending_by_thread: dict[str, deque[ThreadJob]] = {}
        self._active_threads: set[str] = set()
        self._busy_until: dict[str, anyio.Event] = {}

    @staticmethod
    def thread_key(token: ResumeToken) -> str:
        return f"{token.engine}:{token.value}"

    async def note_thread_known(self, token: ResumeToken, done: anyio.Event) -> None:
        key = self.thread_key(token)
        async with self._lock:
            current = self._busy_until.get(key)
            if current is None or current.is_set():
                self._busy_until[key] = done
        self._task_group.start_soon(self._clear_busy, key, done)

    async def enqueue(self, job: ThreadJob) -> None:
        key = self.thread_key(job.resume_token)
        async with self._lock:
            queue = self._pending_by_thread.get(key)
            if queue is None:
                queue = deque()
                self._pending_by_thread[key] = queue
            queue.append(job)
            if key in self._active_threads:
                return
            self._active_threads.add(key)
        self._task_group.start_soon(self._thread_worker, key)

    async def enqueue_resume(
        self,
        chat_id: int,
        user_msg_id: int,
        text: str,
        resume_token: ResumeToken,
    ) -> None:
        await self.enqueue(
            ThreadJob(
                chat_id=chat_id,
                user_msg_id=user_msg_id,
                text=text,
                resume_token=resume_token,
            )
        )

    async def _clear_busy(self, key: str, done: anyio.Event) -> None:
        await done.wait()
        async with self._lock:
            if self._busy_until.get(key) is done:
                self._busy_until.pop(key, None)

    async def _thread_worker(self, key: str) -> None:
        try:
            while True:
                async with self._lock:
                    done = self._busy_until.get(key)
                    queue = self._pending_by_thread.get(key)
                    if not queue:
                        self._pending_by_thread.pop(key, None)
                        self._active_threads.discard(key)
                        return
                    job = queue.popleft()

                if done is not None and not done.is_set():
                    await done.wait()

                await self._run_job(job)
        finally:
            async with self._lock:
                self._active_threads.discard(key)
