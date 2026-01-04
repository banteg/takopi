import anyio
import pytest

from takopi.model import ResumeToken
from takopi.scheduler import ThreadScheduler, ThreadJob


@pytest.mark.anyio
async def test_scheduler_thread_key() -> None:
    token = ResumeToken(engine="codex", value="test-token")
    key = ThreadScheduler.thread_key(token)
    assert key == "codex:test-token"


@pytest.mark.anyio
async def test_scheduler_enqueue_and_run_job() -> None:
    results = []

    async def run_job(job: ThreadJob) -> None:
        results.append(job.text)

    class MockTaskGroup:
        def __init__(self):
            self.tasks = []

        def start_soon(self, func, *args):
            self.tasks.append((func, args))

    tg = MockTaskGroup()
    scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

    token = ResumeToken(engine="codex", value="test-token")
    job = ThreadJob(
        chat_id=123,
        user_msg_id=456,
        text="test message",
        resume_token=token,
    )

    await scheduler.enqueue(job)

    assert len(scheduler._active_threads) == 1
    assert "codex:test-token" in scheduler._pending_by_thread


@pytest.mark.anyio
async def test_scheduler_enqueue_resume() -> None:
    results = []

    async def run_job(job: ThreadJob) -> None:
        results.append(job.text)

    class MockTaskGroup:
        def start_soon(self, func, *args):
            pass

    tg = MockTaskGroup()
    scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

    token = ResumeToken(engine="codex", value="test-token")

    await scheduler.enqueue_resume(
        chat_id=123,
        user_msg_id=456,
        text="resume message",
        resume_token=token,
    )

    assert "codex:test-token" in scheduler._pending_by_thread


@pytest.mark.anyio
async def test_scheduler_note_thread_known() -> None:
    class MockTaskGroup:
        def start_soon(self, func, *args):
            pass

    tg = MockTaskGroup()
    scheduler = ThreadScheduler(task_group=tg, run_job=lambda job: None)

    token = ResumeToken(engine="codex", value="test-token")
    done = anyio.Event()

    await scheduler.note_thread_known(token, done)

    assert "codex:test-token" in scheduler._busy_until


@pytest.mark.anyio
async def test_scheduler_clear_busy() -> None:
    class MockTaskGroup:
        def start_soon(self, func, *args):
            pass

    tg = MockTaskGroup()
    scheduler = ThreadScheduler(task_group=tg, run_job=lambda job: None)

    token = ResumeToken(engine="codex", value="test-token")
    done = anyio.Event()

    await scheduler.note_thread_known(token, done)

    # Check that the event is stored
    assert "codex:test-token" in scheduler._busy_until
    assert scheduler._busy_until["codex:test-token"] is done


@pytest.mark.anyio
async def test_scheduler_multiple_jobs_same_thread() -> None:
    results = []

    async def run_job(job: ThreadJob) -> None:
        results.append(job.text)

    class MockTaskGroup:
        def __init__(self):
            self.tasks = []

        def start_soon(self, func, *args):
            self.tasks.append((func, args))

    tg = MockTaskGroup()
    scheduler = ThreadScheduler(task_group=tg, run_job=run_job)

    token = ResumeToken(engine="codex", value="test-token")

    job1 = ThreadJob(
        chat_id=123,
        user_msg_id=456,
        text="message 1",
        resume_token=token,
    )

    job2 = ThreadJob(
        chat_id=123,
        user_msg_id=457,
        text="message 2",
        resume_token=token,
    )

    await scheduler.enqueue(job1)
    await scheduler.enqueue(job2)

    queue = scheduler._pending_by_thread["codex:test-token"]
    assert len(queue) == 2
