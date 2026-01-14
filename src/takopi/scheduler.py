from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol
from collections.abc import Awaitable, Callable

import anyio

from .context import RunContext
from .logging import get_logger
from .model import ResumeToken
from .transport import ChannelId, MessageId, MessageRef, ThreadId

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ThreadJob:
    chat_id: ChannelId
    user_msg_id: MessageId
    text: str
    resume_token: ResumeToken
    context: RunContext | None = None
    thread_id: ThreadId | None = None
    session_key: tuple[int, int | None] | None = None
    progress_ref: MessageRef | None = None


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
        self._queued_by_progress: dict[tuple[ChannelId, MessageId], ThreadJob] = {}
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
            if job.progress_ref is not None:
                progress_key = (job.chat_id, job.progress_ref.message_id)
                self._queued_by_progress[progress_key] = job
            if key in self._active_threads:
                return
            self._active_threads.add(key)
        self._task_group.start_soon(self._thread_worker, key)

    async def enqueue_resume(
        self,
        chat_id: ChannelId,
        user_msg_id: MessageId,
        text: str,
        resume_token: ResumeToken,
        context: RunContext | None = None,
        thread_id: ThreadId | None = None,
        session_key: tuple[int, int | None] | None = None,
        progress_ref: MessageRef | None = None,
    ) -> None:
        await self.enqueue(
            ThreadJob(
                chat_id=chat_id,
                user_msg_id=user_msg_id,
                text=text,
                resume_token=resume_token,
                context=context,
                thread_id=thread_id,
                session_key=session_key,
                progress_ref=progress_ref,
            )
        )

    async def cancel_queued(
        self, chat_id: ChannelId, progress_msg_id: MessageId
    ) -> ThreadJob | None:
        progress_key = (chat_id, progress_msg_id)
        async with self._lock:
            job = self._queued_by_progress.pop(progress_key, None)
            if job is None:
                return None
            thread_key = self.thread_key(job.resume_token)
            queue = self._pending_by_thread.get(thread_key)
            if queue is None:
                return None
            try:
                queue.remove(job)
            except ValueError:
                return None
            if not queue:
                self._pending_by_thread.pop(thread_key, None)
            return job

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
                    if job.progress_ref is not None:
                        progress_key = (job.chat_id, job.progress_ref.message_id)
                        self._queued_by_progress.pop(progress_key, None)

                if done is not None and not done.is_set():
                    await done.wait()

                try:
                    await self._run_job(job)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "scheduler.job_failed",
                        key=key,
                        tag=job.resume_token.engine,
                        chat_id=job.chat_id,
                        user_msg_id=job.user_msg_id,
                        error=str(exc),
                        error_type=exc.__class__.__name__,
                    )
        finally:
            async with self._lock:
                self._active_threads.discard(key)
