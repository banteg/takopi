from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shlex
import shutil
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from typing import Any

import typer

from .config import load_telegram_config
from .constants import TELEGRAM_HARD_LIMIT
from .exec_render import ExecProgressRenderer, render_event_cli
from .rendering import render_markdown
from .telegram_client import TelegramClient

logger = logging.getLogger("exec_bridge")
UUID_PATTERN = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def extract_session_id(text: str | None) -> str | None:
    if not text:
        return None
    if m := UUID_PATTERN.search(text):
        return m.group(0)
    return None


async def _drain_stderr(stderr: asyncio.StreamReader | None, tail: deque[str]) -> None:
    if stderr is None:
        return
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


def setup_logging(log_file: str | None) -> None:
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.debug("[debug] file logger initialized path=%r", log_file)


TELEGRAM_TEXT_LIMIT = TELEGRAM_HARD_LIMIT
TELEGRAM_MARKDOWN_LIMIT = 3500


def _clamp_tg_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
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


async def _send_markdown(
    bot: TelegramClient,
    *,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    rendered, entities = render_markdown(text)
    if len(rendered) > TELEGRAM_MARKDOWN_LIMIT:
        rendered = truncate_for_telegram(rendered, TELEGRAM_MARKDOWN_LIMIT)
        entities = []

    return await bot.send_message(
        chat_id=chat_id,
        text=rendered,
        entities=entities or None,
        reply_to_message_id=reply_to_message_id,
        disable_notification=disable_notification,
    )


EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class CodexExecRunner:
    """
    Runs Codex in non-interactive mode:
      - new:    codex exec --json ... -
      - resume: codex exec --json ... resume <SESSION_ID> -
    """

    def __init__(
        self, codex_cmd: str, workspace: str | None, extra_args: list[str]
    ) -> None:
        self.codex_cmd = codex_cmd
        self.workspace = workspace
        self.extra_args = extra_args

        # Per-session locks to prevent concurrent resumes to the same session_id.
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._locks_guard:
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
        logger.info(
            "[codex] start run session_id=%r workspace=%r", session_id, self.workspace
        )
        args = [self.codex_cmd, "exec", "--json"]
        args.extend(self.extra_args)
        if self.workspace:
            args.extend(["--cd", self.workspace])

        # Always pipe prompt via stdin ("-") to avoid quoting issues.
        if session_id:
            args.extend(["resume", session_id, "-"])
        else:
            args.append("-")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin and proc.stdout and proc.stderr
        logger.debug("[codex] spawn pid=%s args=%r", proc.pid, args)

        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        stderr_tail: deque[str] = deque(maxlen=200)
        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr, stderr_tail))

        found_session: str | None = session_id
        last_agent_text: str | None = None
        saw_agent_message = False
        cli_last_turn: int | None = None

        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                cli_last_turn, out_lines = render_event_cli(evt, cli_last_turn)
                for out in out_lines:
                    logger.info("[codex] %s", out)

                if on_event is not None:
                    try:
                        res = on_event(evt)
                        if inspect.isawaitable(res):
                            await res
                    except Exception as e:
                        logger.info("[codex][on_event] callback error: %s", e)

                if evt.get("type") == "thread.started":
                    found_session = evt.get("thread_id") or found_session

                if evt.get("type") == "item.completed":
                    item = evt.get("item") or {}
                    if item.get("type") == "agent_message" and isinstance(
                        item.get("text"), str
                    ):
                        last_agent_text = item["text"]
                        saw_agent_message = True
        except asyncio.CancelledError:
            proc.terminate()
            raise
        finally:
            rc = await proc.wait()
            await stderr_task

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
        """
        If resuming, serialize per-session.
        """
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
    ignore_backlog: bool
    progress_edit_every_s: float
    progress_silent: bool
    final_notify: bool
    startup_msg: str
    max_concurrency: int


def _parse_bridge_config(
    *,
    progress_edit_every_s: float,
    progress_silent: bool,
    final_notify: bool,
    ignore_backlog: bool,
    cd: str | None,
    model: str | None,
) -> BridgeConfig:
    config = load_telegram_config()
    token = config["bot_token"]
    chat_id = int(config["chat_id"])

    startup_pwd = os.getcwd()
    startup_msg = f"codex exec bridge has started\npwd: {startup_pwd}"

    codex_cmd = shutil.which("codex")
    if not codex_cmd:
        raise RuntimeError("codex not found on PATH")

    workspace = cd if cd is not None else config.get("cd")
    raw_exec_args = config.get("codex_exec_args", "")
    if isinstance(raw_exec_args, list):
        extra_args = [str(v) for v in raw_exec_args]
    else:
        extra_args = shlex.split(str(raw_exec_args))  # e.g. "--full-auto --search"

    if model:
        extra_args.extend(["--model", model])

    def _has_notify_override(args: list[str]) -> bool:
        for i, arg in enumerate(args):
            if arg in ("-c", "--config"):
                key = args[i + 1].split("=", 1)[0].strip()
                if key == "notify" or key.endswith(".notify"):
                    return True
            elif arg.startswith(("--config=", "-c=")):
                key = arg.split("=", 1)[1].split("=", 1)[0].strip()
                if key == "notify" or key.endswith(".notify"):
                    return True
        return False

    if not _has_notify_override(extra_args):
        extra_args.extend(["-c", "notify=[]"])

    bot = TelegramClient(token)
    runner = CodexExecRunner(codex_cmd=codex_cmd, workspace=workspace, extra_args=extra_args)

    return BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=chat_id,
        ignore_backlog=bool(ignore_backlog),
        progress_edit_every_s=progress_edit_every_s,
        progress_silent=progress_silent,
        final_notify=final_notify,
        startup_msg=startup_msg,
        max_concurrency=16,
    )


async def _send_startup(cfg: BridgeConfig) -> None:
    try:
        await cfg.bot.send_message(chat_id=cfg.chat_id, text=cfg.startup_msg)
        logger.info("[startup] sent startup message to chat_id=%s", cfg.chat_id)
    except Exception as e:
        logger.info(
            "[startup] failed to send startup message to chat_id=%s: %s", cfg.chat_id, e
        )


async def _drain_backlog(cfg: BridgeConfig, offset: int | None) -> int | None:
    if not cfg.ignore_backlog:
        return offset
    try:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=0, allowed_updates=["message"]
        )
    except Exception as e:
        logger.info("[startup] backlog drain failed: %s", e)
        return offset
    if updates:
        offset = updates[-1]["update_id"] + 1
        logger.info("[startup] drained %s pending update(s)", len(updates))
    return offset


async def _handle_message(
    cfg: BridgeConfig,
    *,
    semaphore: asyncio.Semaphore,
    chat_id: int,
    user_msg_id: int,
    text: str,
    resume_session: str | None,
) -> None:
    started_at = time.monotonic()
    progress_renderer = ExecProgressRenderer(max_actions=5)

    progress_id: int | None = None

    last_edit_at = 0.0
    edit_task: asyncio.Task[None] | None = None

    async def _edit_progress(md: str) -> None:
        if progress_id is None:
            return
        rendered, entities = render_markdown(md)
        if len(rendered) > TELEGRAM_TEXT_LIMIT:
            rendered = truncate_for_telegram(rendered, TELEGRAM_TEXT_LIMIT)
            entities = []
        try:
            await cfg.bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_id,
                text=rendered,
                entities=entities or None,
            )
        except Exception as e:
            logger.info(
                "[progress] edit failed chat_id=%s message_id=%s: %s",
                chat_id,
                progress_id,
                e,
            )

    try:
        initial_md = progress_renderer.render_progress(0.0)
        initial_rendered, initial_entities = render_markdown(initial_md)
        if len(initial_rendered) > TELEGRAM_TEXT_LIMIT:
            initial_rendered = truncate_for_telegram(initial_rendered, TELEGRAM_TEXT_LIMIT)
            initial_entities = []
        progress_msg = await cfg.bot.send_message(
            chat_id=chat_id,
            text=initial_rendered,
            entities=initial_entities or None,
            reply_to_message_id=user_msg_id,
            disable_notification=cfg.progress_silent,
        )
        progress_id = int(progress_msg["message_id"])
        last_edit_at = time.monotonic()
        logger.debug("[progress] sent chat_id=%s message_id=%s", chat_id, progress_id)
    except Exception as e:
        logger.info(
            "[handle] failed to send progress message chat_id=%s: %s", chat_id, e
        )

    async def on_event(evt: dict[str, Any]) -> None:
        nonlocal last_edit_at, edit_task
        if progress_id is None:
            return
        if not progress_renderer.note_event(evt):
            return
        now = time.monotonic()
        if (now - last_edit_at) < cfg.progress_edit_every_s:
            return
        if edit_task is not None and not edit_task.done():
            return
        last_edit_at = now
        elapsed = now - started_at
        edit_task = asyncio.create_task(
            _edit_progress(progress_renderer.render_progress(elapsed))
        )

    async with semaphore:
        try:
            session_id, answer, saw_agent_message = await cfg.runner.run_serialized(
                text, resume_session, on_event=on_event
            )
        except Exception as e:
            if edit_task is not None:
                await asyncio.gather(edit_task, return_exceptions=True)

            err = _clamp_tg_text(f"Error:\n{e}")
            if progress_id is not None and len(err) <= TELEGRAM_TEXT_LIMIT:
                try:
                    await cfg.bot.edit_message_text(
                        chat_id=chat_id, message_id=progress_id, text=err
                    )
                    return
                except Exception:
                    pass
            await _send_markdown(
                cfg.bot,
                chat_id=chat_id,
                text=err,
                reply_to_message_id=user_msg_id,
                disable_notification=cfg.progress_silent,
            )
            return

    if edit_task is not None:
        await asyncio.gather(edit_task, return_exceptions=True)

    answer = answer or "(No agent_message captured from JSON stream.)"
    elapsed = time.monotonic() - started_at
    status = "done" if saw_agent_message else "error"
    final_md = (
        progress_renderer.render_final(elapsed, answer, status=status)
        + f"\n\nresume: `{session_id}`"
    )
    final_rendered, final_entities = render_markdown(final_md)
    can_edit_final = progress_id is not None and len(final_rendered) <= TELEGRAM_TEXT_LIMIT

    if cfg.final_notify or not can_edit_final:
        await _send_markdown(
            cfg.bot,
            chat_id=chat_id,
            text=final_md,
            reply_to_message_id=user_msg_id,
            disable_notification=False,
        )
        if progress_id is not None:
            try:
                await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)
            except Exception:
                pass
    else:
        await cfg.bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_id,
            text=final_rendered,
            entities=final_entities or None,
        )


async def _run_main_loop(cfg: BridgeConfig) -> None:
    semaphore = asyncio.Semaphore(cfg.max_concurrency)

    tasks: set[asyncio.Task[None]] = set()

    def _task_done(task: asyncio.Task[None]) -> None:
        tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("[handle] task failed")

    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    try:
        while True:
            try:
                updates = await cfg.bot.get_updates(
                    offset=offset, timeout_s=50, allowed_updates=["message"]
                )
            except Exception as e:
                logger.info("[loop] getUpdates failed: %s", e)
                await asyncio.sleep(2)
                continue

            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                msg_chat_id = msg.get("chat", {}).get("id")
                if "text" not in msg:
                    continue
                if int(msg_chat_id) != cfg.chat_id:
                    continue
                if msg.get("from", {}).get("is_bot"):
                    continue

                text = msg["text"]
                user_msg_id = msg["message_id"]
                resume_session = extract_session_id(text)
                r = msg.get("reply_to_message") or {}
                resume_session = resume_session or extract_session_id(r.get("text"))

                task = asyncio.create_task(
                    _handle_message(
                        cfg,
                        semaphore=semaphore,
                        chat_id=msg_chat_id,
                        user_msg_id=user_msg_id,
                        text=text,
                        resume_session=resume_session,
                    )
                )
                tasks.add(task)
                task.add_done_callback(_task_done)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await cfg.bot.close()


def run(
    progress_edit_every_s: float = typer.Option(
        2.0,
        "--progress-edit-every",
        help="Minimum seconds between progress message edits.",
        min=1.0,
    ),
    progress_silent: bool = typer.Option(
        True,
        "--progress-silent/--no-progress-silent",
        help="Send the progress message without sound/vibration.",
    ),
    final_notify: bool = typer.Option(
        True,
        "--final-notify/--no-final-notify",
        help="Send the final response as a new message (not an edit).",
    ),
    ignore_backlog: bool = typer.Option(
        True,
        "--ignore-backlog/--process-backlog",
        help="Skip pending Telegram updates that arrived before startup.",
    ),
    log_file: str | None = typer.Option(
        "exec_bridge.log",
        "--log-file",
        help="Write detailed debug logs to this file (set to empty to disable).",
    ),
    cd: str | None = typer.Option(
        None,
        "--cd",
        help="Pass through to `codex --cd` (defaults to `cd` in ~/.codex/telegram.toml).",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Codex model to pass to `codex exec`.",
    ),
) -> None:
    setup_logging(log_file if log_file else None)
    cfg = _parse_bridge_config(
        progress_edit_every_s=progress_edit_every_s,
        progress_silent=progress_silent,
        final_notify=final_notify,
        ignore_backlog=ignore_backlog,
        cd=cd,
        model=model,
    )
    asyncio.run(_run_main_loop(cfg))


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
