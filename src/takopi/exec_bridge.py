from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shutil
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from weakref import WeakValueDictionary

import typer

from . import __version__
from .config import ConfigError, load_telegram_config
from .exec_render import (
    ExecProgressRenderer,
    render_event_cli,
    render_markdown,
)
from .logging import setup_logging
from .onboarding import check_setup, render_setup_guide
from .telegram import TelegramClient


logger = logging.getLogger(__name__)
UUID_PATTERN_TEXT = r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
UUID_PATTERN = re.compile(UUID_PATTERN_TEXT, re.IGNORECASE)
RESUME_LINE = re.compile(
    rf"^\s*resume\s*:\s*`?(?P<id>{UUID_PATTERN_TEXT})`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


def extract_session_id(text: str | None) -> str | None:
    if not text:
        return None
    found: str | None = None
    for match in RESUME_LINE.finditer(text):
        found = match.group("id")
    return found


def resolve_resume_session(text: str | None, reply_text: str | None) -> str | None:
    return extract_session_id(text) or extract_session_id(reply_text)


async def _drain_stderr(stderr: asyncio.StreamReader, tail: deque[str]) -> None:
    try:
        while True:
            line = await stderr.readline()
            if not line:
                return
            decoded = line.decode(errors="replace")
            logger.info("[codex][stderr] %s", decoded.rstrip())
            tail.append(decoded)
    except Exception as e:
        logger.debug("[codex][stderr] drain error: %s", e)


@asynccontextmanager
async def manage_subprocess(*args, **kwargs):
    proc = await asyncio.create_subprocess_exec(*args, **kwargs)
    try:
        yield proc
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


TELEGRAM_MARKDOWN_LIMIT = 3500
PROGRESS_EDIT_EVERY_S = 2.0


def _clamp_tg_text(text: str, limit: int = TELEGRAM_MARKDOWN_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...(truncated)"


def truncate_for_telegram(text: str, limit: int) -> str:
    """
    Truncate text to fit Telegram limits while preserving the trailing `resume: ...`
    line (if present), otherwise preserving the last non-empty line.
    """
    if len(text) <= limit:
        return text

    lines = text.splitlines()

    tail_lines: list[str] | None = None
    is_resume_tail = False
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if "resume" in line and UUID_PATTERN.search(line):
            tail_lines = lines[i:]
            is_resume_tail = True
            break

    if tail_lines is None:
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                tail_lines = [lines[i]]
                break

    tail = "\n".join(tail_lines or []).strip("\n")
    sep = "\n…\n"

    max_tail = limit if is_resume_tail else (limit // 4)
    tail = tail[-max_tail:] if max_tail > 0 else ""

    head_budget = limit - len(sep) - len(tail)
    if head_budget <= 0:
        return tail[-limit:] if tail else text[:limit]

    head = text[:head_budget].rstrip()
    return (head + sep + tail)[:limit]


def prepare_telegram(md: str, *, limit: int) -> tuple[str, list[dict[str, Any]] | None]:
    rendered, entities = render_markdown(md)
    if len(rendered) > limit:
        rendered = truncate_for_telegram(rendered, limit)
        return rendered, None
    return rendered, entities


async def _send_or_edit_markdown(
    bot: TelegramClient,
    *,
    chat_id: int,
    text: str,
    edit_message_id: int | None = None,
    reply_to_message_id: int | None = None,
    disable_notification: bool = False,
    limit: int = TELEGRAM_MARKDOWN_LIMIT,
) -> tuple[dict[str, Any], bool]:
    if edit_message_id is not None:
        rendered, entities = prepare_telegram(text, limit=limit)
        try:
            return (
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=edit_message_id,
                    text=rendered,
                    entities=entities,
                ),
                True,
            )
        except Exception as e:
            logger.info(
                "[tg] edit failed chat_id=%s message_id=%s: %s",
                chat_id,
                edit_message_id,
                e,
            )

    rendered, entities = prepare_telegram(text, limit=limit)
    return (
        await bot.send_message(
            chat_id=chat_id,
            text=rendered,
            entities=entities,
            reply_to_message_id=reply_to_message_id,
            disable_notification=disable_notification,
        ),
        False,
    )


EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class CodexExecRunner:
    def __init__(
        self,
        codex_cmd: str,
        extra_args: list[str],
    ) -> None:
        self.codex_cmd = codex_cmd
        self.extra_args = extra_args

        # Per-session locks to prevent concurrent resumes to the same session_id.
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def run(
        self,
        prompt: str,
        session_id: str | None,
        on_event: EventCallback | None = None,
    ) -> tuple[str, str, bool]:
        logger.info("[codex] start run session_id=%r", session_id)
        logger.debug("[codex] prompt: %s", prompt)
        args = [self.codex_cmd]
        args.extend(self.extra_args)
        args.extend(["exec", "--json"])

        # Always pipe prompt via stdin ("-") to avoid quoting issues.
        if session_id:
            args.extend(["resume", session_id, "-"])
        else:
            args.append("-")

        async with manage_subprocess(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ) as proc:
            proc_stdin = cast(asyncio.StreamWriter, proc.stdin)
            proc_stdout = cast(asyncio.StreamReader, proc.stdout)
            proc_stderr = cast(asyncio.StreamReader, proc.stderr)
            logger.debug("[codex] spawn pid=%s args=%r", proc.pid, args)

            stderr_tail: deque[str] = deque(maxlen=200)
            stderr_task = asyncio.create_task(_drain_stderr(proc_stderr, stderr_tail))

            found_session: str | None = session_id
            last_agent_text: str | None = None
            saw_agent_message = False
            cli_last_item: int | None = None

            cancelled = False
            rc: int | None = None

            try:
                proc_stdin.write(prompt.encode())
                await proc_stdin.drain()
                proc_stdin.close()

                async for raw_line in proc_stdout:
                    raw = raw_line.decode(errors="replace")
                    logger.debug("[codex][jsonl] %s", raw.rstrip("\n"))
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("[codex][jsonl] invalid line: %r", line)
                        continue

                    cli_last_item, out_lines = render_event_cli(evt, cli_last_item)
                    for out in out_lines:
                        logger.info("[codex] %s", out)

                    if on_event is not None:
                        try:
                            res = on_event(evt)
                            if inspect.isawaitable(res):
                                await res
                        except Exception as e:
                            logger.info("[codex][on_event] callback error: %s", e)

                    if evt["type"] == "thread.started":
                        found_session = evt.get("thread_id") or found_session

                    if evt["type"] == "item.completed":
                        item = evt.get("item") or {}
                        if item.get("type") == "agent_message" and isinstance(
                            item.get("text"), str
                        ):
                            last_agent_text = item["text"]
                            saw_agent_message = True
            except asyncio.CancelledError:
                cancelled = True
            finally:
                if cancelled:
                    if not stderr_task.done():
                        stderr_task.cancel()
                    task = cast(asyncio.Task, asyncio.current_task())
                    while task.cancelling():
                        task.uncancel()
                if not cancelled:
                    rc = await proc.wait()
                await asyncio.gather(stderr_task, return_exceptions=True)

            if cancelled:
                raise asyncio.CancelledError

            logger.debug("[codex] process exit pid=%s rc=%s", proc.pid, rc)
            if rc != 0:
                tail = "".join(stderr_tail)
                raise RuntimeError(f"codex exec failed (rc={rc}). stderr tail:\n{tail}")

            if not found_session:
                raise RuntimeError(
                    "codex exec finished but no session_id/thread_id was captured"
                )

            logger.info("[codex] done run session_id=%r", found_session)
            return (
                found_session,
                (last_agent_text or "(No agent_message captured from JSON stream.)"),
                saw_agent_message,
            )

    async def run_serialized(
        self,
        prompt: str,
        session_id: str | None,
        on_event: EventCallback | None = None,
    ) -> tuple[str, str, bool]:
        if not session_id:
            return await self.run(prompt, session_id=None, on_event=on_event)
        lock = await self._lock_for(session_id)
        async with lock:
            return await self.run(prompt, session_id=session_id, on_event=on_event)


@dataclass(frozen=True)
class BridgeConfig:
    bot: TelegramClient
    runner: CodexExecRunner
    chat_id: int
    final_notify: bool
    startup_msg: str
    max_concurrency: int


def _parse_bridge_config(
    *,
    final_notify: bool,
    profile: str | None,
) -> BridgeConfig:
    startup_pwd = os.getcwd()

    config, config_path = load_telegram_config()
    try:
        token = config["bot_token"]
    except KeyError:
        raise ConfigError(f"Missing key `bot_token` in {config_path}.") from None
    if not isinstance(token, str) or not token.strip():
        raise ConfigError(
            f"Invalid `bot_token` in {config_path}; expected a non-empty string."
        ) from None
    try:
        chat_id_value = config["chat_id"]
    except KeyError:
        raise ConfigError(f"Missing key `chat_id` in {config_path}.") from None
    if isinstance(chat_id_value, bool) or not isinstance(chat_id_value, int):
        raise ConfigError(
            f"Invalid `chat_id` in {config_path}; expected an integer."
        ) from None
    chat_id = chat_id_value

    codex_cmd = shutil.which("codex")
    if not codex_cmd:
        raise ConfigError(
            "codex not found on PATH. Install the Codex CLI with:\n"
            "  npm install -g @openai/codex\n"
            "  # or on macOS\n"
            "  brew install codex"
        )

    startup_msg = f"🐙 takopi is ready to help-pi!\npwd: {startup_pwd}"
    extra_args = ["-c", "notify=[]"]
    if profile:
        extra_args.extend(["--profile", profile])

    bot = TelegramClient(token)
    runner = CodexExecRunner(codex_cmd=codex_cmd, extra_args=extra_args)

    return BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=chat_id,
        final_notify=final_notify,
        startup_msg=startup_msg,
        max_concurrency=16,
    )


async def _send_startup(cfg: BridgeConfig) -> None:
    try:
        logger.debug("[startup] message: %s", cfg.startup_msg)
        await cfg.bot.send_message(chat_id=cfg.chat_id, text=cfg.startup_msg)
        logger.info("[startup] sent startup message to chat_id=%s", cfg.chat_id)
    except Exception as e:
        logger.info(
            "[startup] failed to send startup message to chat_id=%s: %s", cfg.chat_id, e
        )


async def _drain_backlog(cfg: BridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        try:
            updates = await cfg.bot.get_updates(
                offset=offset, timeout_s=0, allowed_updates=["message"]
            )
        except Exception as e:
            logger.info("[startup] backlog drain failed: %s", e)
            return offset
        logger.debug("[startup] backlog updates: %s", updates)
        if not updates:
            if drained:
                logger.info("[startup] drained %s pending update(s)", drained)
            return offset
        offset = updates[-1]["update_id"] + 1
        drained += len(updates)


async def _handle_message(
    cfg: BridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    text: str,
    resume_session: str | None,
    running_tasks: dict[str, asyncio.Task[Any]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S,
) -> None:
    logger.debug(
        "[handle] incoming chat_id=%s message_id=%s resume=%r text=%s",
        chat_id,
        user_msg_id,
        resume_session,
        text,
    )
    started_at = clock()
    progress_renderer = ExecProgressRenderer(max_actions=5)

    progress_id: int | None = None

    last_edit_at = 0.0
    edit_task: asyncio.Task[None] | None = None
    last_rendered: str | None = None
    pending_rendered: str | None = None

    async def _edit_progress(
        md: str, rendered: str, entities: list[dict[str, Any]] | None
    ) -> None:
        nonlocal last_rendered, pending_rendered
        if progress_id is None:
            return
        logger.debug(
            "[progress] edit message_id=%s md=%s rendered=%s entities=%s",
            progress_id,
            md,
            rendered,
            entities,
        )
        try:
            await cfg.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_id,
                text=rendered,
                entities=entities,
            )
            last_rendered = rendered
        except Exception as e:
            logger.info(
                "[progress] edit failed chat_id=%s message_id=%s: %s",
                chat_id,
                progress_id,
                e,
            )
        finally:
            if pending_rendered == rendered:
                pending_rendered = None

    try:
        initial_md = progress_renderer.render_progress(0.0)
        initial_rendered, initial_entities = prepare_telegram(
            initial_md, limit=TELEGRAM_MARKDOWN_LIMIT
        )
        logger.debug(
            "[progress] send reply_to=%s md=%s rendered=%s entities=%s",
            user_msg_id,
            initial_md,
            initial_rendered,
            initial_entities,
        )
        progress_msg = await cfg.bot.send_message(
            chat_id=chat_id,
            text=initial_rendered,
            entities=initial_entities,
            reply_to_message_id=user_msg_id,
            disable_notification=True,
        )
        progress_id = int(progress_msg["message_id"])
        last_edit_at = clock()
        last_rendered = initial_rendered
        logger.debug("[progress] sent chat_id=%s message_id=%s", chat_id, progress_id)
    except Exception as e:
        logger.info(
            "[handle] failed to send progress message chat_id=%s: %s", chat_id, e
        )

    exec_task: asyncio.Task[tuple[str, str, bool]] | None = None
    tracked_session_id: str | None = None

    async def on_event(evt: dict[str, Any]) -> None:
        nonlocal last_edit_at, edit_task, pending_rendered, tracked_session_id
        if progress_id is None:
            return
        if not progress_renderer.note_event(evt):
            return

        if (
            evt["type"] == "thread.started"
            and running_tasks is not None
            and exec_task is not None
        ):
            tracked_session_id = progress_renderer.resume_session
            if tracked_session_id:
                running_tasks[tracked_session_id] = exec_task

        now = clock()
        if (now - last_edit_at) < progress_edit_every:
            return
        if edit_task is not None and not edit_task.done():
            return
        elapsed = now - started_at
        md = progress_renderer.render_progress(elapsed)
        rendered, entities = prepare_telegram(md, limit=TELEGRAM_MARKDOWN_LIMIT)
        if rendered == last_rendered or rendered == pending_rendered:
            return
        last_edit_at = now
        pending_rendered = rendered
        edit_task = asyncio.create_task(_edit_progress(md, rendered, entities))

    exec_task = asyncio.create_task(
        cfg.runner.run_serialized(text, resume_session, on_event=on_event)
    )

    cancelled = False
    try:
        session_id, answer, saw_agent_message = await exec_task
    except asyncio.CancelledError:
        cancelled = True
        session_id = tracked_session_id or resume_session
    except Exception as e:
        if edit_task is not None:
            await asyncio.gather(edit_task, return_exceptions=True)

        err = _clamp_tg_text(f"Error:\n{e}")
        logger.debug("[error] send reply_to=%s text=%s", user_msg_id, err)
        await _send_or_edit_markdown(
            cfg.bot,
            chat_id=chat_id,
            text=err,
            edit_message_id=progress_id,
            reply_to_message_id=user_msg_id,
            disable_notification=True,
            limit=TELEGRAM_MARKDOWN_LIMIT,
        )
        return
    finally:
        if tracked_session_id and running_tasks is not None and exec_task is not None:
            # Avoid removing a newer task for the same session_id if another run
            # registered while this one was finishing.
            if running_tasks.get(tracked_session_id) is exec_task:
                running_tasks.pop(tracked_session_id, None)

    if edit_task is not None:
        await asyncio.gather(edit_task, return_exceptions=True)

    elapsed = clock() - started_at
    if cancelled:
        logger.info(
            "[handle] cancelled session_id=%s elapsed=%.1fs", session_id, elapsed
        )
        progress_renderer.resume_session = session_id
        final_md = progress_renderer.render_progress(elapsed, label="`cancelled`")
        await _send_or_edit_markdown(
            cfg.bot,
            chat_id=chat_id,
            text=final_md,
            edit_message_id=progress_id,
            reply_to_message_id=user_msg_id,
            disable_notification=True,
            limit=TELEGRAM_MARKDOWN_LIMIT,
        )
        return

    status = "done" if saw_agent_message else "error"
    progress_renderer.resume_session = session_id
    final_md = progress_renderer.render_final(elapsed, answer, status=status)
    logger.debug("[final] markdown: %s", final_md)
    final_rendered, final_entities = render_markdown(final_md)
    can_edit_final = (
        progress_id is not None and len(final_rendered) <= TELEGRAM_MARKDOWN_LIMIT
    )
    edit_message_id = None if cfg.final_notify or not can_edit_final else progress_id

    if edit_message_id is None:
        logger.debug(
            "[final] send reply_to=%s rendered=%s entities=%s",
            user_msg_id,
            final_rendered,
            final_entities,
        )
    else:
        logger.debug(
            "[final] edit message_id=%s rendered=%s entities=%s",
            edit_message_id,
            final_rendered,
            final_entities,
        )

    _, edited = await _send_or_edit_markdown(
        cfg.bot,
        chat_id=chat_id,
        text=final_md,
        edit_message_id=edit_message_id,
        reply_to_message_id=user_msg_id,
        disable_notification=False,
        limit=TELEGRAM_MARKDOWN_LIMIT,
    )
    if progress_id is not None and (edit_message_id is None or not edited):
        try:
            logger.debug("[final] delete progress message_id=%s", progress_id)
            await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)
        except Exception:
            pass


async def poll_updates(cfg: BridgeConfig):
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    while True:
        try:
            updates = await cfg.bot.get_updates(
                offset=offset, timeout_s=50, allowed_updates=["message"]
            )
        except Exception as e:
            logger.info("[loop] getUpdates failed: %s", e)
            await asyncio.sleep(2)
            continue
        logger.debug("[loop] updates: %s", updates)

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd["message"]
            if "text" not in msg:
                continue
            if not (msg["chat"]["id"] == msg["from"]["id"] == cfg.chat_id):
                continue
            yield msg


async def _handle_cancel(
    cfg: BridgeConfig,
    msg: dict[str, Any],
    running_tasks: dict[str, asyncio.Task[Any]],
) -> None:
    chat_id = msg["chat"]["id"]
    user_msg_id = msg["message_id"]
    reply = msg.get("reply_to_message")

    if not reply:
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="reply to the progress message to cancel.",
            reply_to_message_id=user_msg_id,
        )
        return

    session_id = extract_session_id(reply.get("text"))
    if not session_id:
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="nothing is currently running for that message.",
            reply_to_message_id=user_msg_id,
        )
        return

    task = running_tasks.get(session_id)
    if not task or task.done():
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="nothing is currently running for that message.",
            reply_to_message_id=user_msg_id,
        )
        return

    logger.info("[cancel] cancelling session_id=%s", session_id)
    task.cancel()


async def _run_main_loop(cfg: BridgeConfig) -> None:
    worker_count = max(1, min(cfg.max_concurrency, 16))
    queue: asyncio.Queue[tuple[int, int, str, str | None]] = asyncio.Queue(
        maxsize=worker_count * 2
    )
    running_tasks: dict[str, asyncio.Task[Any]] = {}

    async def worker() -> None:
        while True:
            chat_id, user_msg_id, text, resume_session = await queue.get()
            try:
                await _handle_message(
                    cfg,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    text=text,
                    resume_session=resume_session,
                    running_tasks=running_tasks,
                )
            except Exception:
                logger.exception("[handle] worker failed")
            finally:
                queue.task_done()

    try:
        async with asyncio.TaskGroup() as tg:
            for _ in range(worker_count):
                tg.create_task(worker())
            async for msg in poll_updates(cfg):
                text = msg["text"]
                user_msg_id = msg["message_id"]

                if text == "/cancel":
                    tg.create_task(_handle_cancel(cfg, msg, running_tasks))
                    continue

                r = msg.get("reply_to_message") or {}
                resume_session = resolve_resume_session(text, r.get("text"))

                await queue.put((msg["chat"]["id"], user_msg_id, text, resume_session))
    finally:
        await cfg.bot.close()


def run(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    final_notify: bool = typer.Option(
        True,
        "--final-notify/--no-final-notify",
        help="Send the final response as a new message (not an edit).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug/--no-debug",
        help="Log codex JSONL, Telegram requests, and rendered messages.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Codex profile name to pass to `codex --profile`.",
    ),
) -> None:
    setup_logging(debug=debug)
    setup = check_setup()
    if not setup.ok:
        render_setup_guide(setup)
        raise typer.Exit(code=1)
    try:
        cfg = _parse_bridge_config(
            final_notify=final_notify,
            profile=profile,
        )
    except ConfigError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    asyncio.run(_run_main_loop(cfg))


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
