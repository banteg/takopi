from __future__ import annotations

import logging
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
import typer

from . import __version__
from .config import ConfigError, load_telegram_config
from .exec_render import ExecProgressRenderer, render_markdown
from .logging import setup_logging
from .onboarding import check_setup, render_setup_guide
from .telegram import TelegramClient
from .runners.base import ResumeToken, Runner, TakopiEvent
from .runners.codex import CodexRunner


logger = logging.getLogger(__name__)
RESUME_LINE = re.compile(
    r"^\s*resume\s*:\s*`(?P<token>[^`\s]+)`\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _print_version_and_exit() -> None:
    typer.echo(__version__)
    raise typer.Exit()


def _version_callback(value: bool) -> None:
    if value:
        _print_version_and_exit()


def extract_resume_token(text: str | None) -> str | None:
    if not text:
        return None
    found: str | None = None
    for match in RESUME_LINE.finditer(text):
        found = match.group("token")
    return found


def resolve_resume_token(text: str | None, reply_text: str | None) -> str | None:
    return extract_resume_token(text) or extract_resume_token(reply_text)


TELEGRAM_MARKDOWN_LIMIT = 3500
PROGRESS_EDIT_EVERY_S = 1.0


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
        if RESUME_LINE.match(line):
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
) -> tuple[dict[str, Any] | None, bool]:
    if edit_message_id is not None:
        rendered, entities = prepare_telegram(text, limit=limit)
        edited = await bot.edit_message_text(
            chat_id=chat_id,
            message_id=edit_message_id,
            text=rendered,
            entities=entities,
        )
        if edited is not None:
            return (edited, True)

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


class ProgressEdits:
    def __init__(
        self,
        *,
        bot: TelegramClient,
        chat_id: int,
        progress_id: int | None,
        renderer: ExecProgressRenderer,
        started_at: float,
        progress_edit_every: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        limit: int,
        last_edit_at: float,
        last_rendered: str | None,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.progress_id = progress_id
        self.renderer = renderer
        self.started_at = started_at
        self.progress_edit_every = progress_edit_every
        self.clock = clock
        self.sleep = sleep
        self.limit = limit
        self.last_edit_at = last_edit_at
        self.last_rendered = last_rendered
        self._event_seq = 0
        self._published_seq = 0
        self.wakeup = anyio.Event()

    async def _wait_for_wakeup(self) -> None:
        await self.wakeup.wait()
        self.wakeup = anyio.Event()

    async def run(self) -> None:
        if self.progress_id is None:
            return
        while True:
            await self._wait_for_wakeup()
            while self._published_seq < self._event_seq:
                await self.sleep(
                    max(
                        0.0,
                        self.last_edit_at + self.progress_edit_every - self.clock(),
                    )
                )

                seq_at_render = self._event_seq
                now = self.clock()
                md = self.renderer.render_progress(now - self.started_at)
                rendered, entities = prepare_telegram(md, limit=self.limit)
                if rendered != self.last_rendered:
                    logger.debug(
                        "[progress] edit message_id=%s md=%s", self.progress_id, md
                    )
                    self.last_edit_at = now
                    edited = await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.progress_id,
                        text=rendered,
                        entities=entities,
                    )
                    if edited is not None:
                        self.last_rendered = rendered

                self._published_seq = seq_at_render

    async def on_event(self, evt: TakopiEvent) -> None:
        if not self.renderer.note_event(evt):
            return
        if self.progress_id is None:
            return
        self._event_seq += 1
        self.wakeup.set()


@dataclass(frozen=True)
class BridgeConfig:
    bot: TelegramClient
    runner: Runner
    chat_id: int
    final_notify: bool
    startup_msg: str
    max_concurrency: int


@dataclass
class RunningTask:
    scope: anyio.CancelScope


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

    codex_cfg = config.get("codex") or {}
    if not isinstance(codex_cfg, dict):
        raise ConfigError(f"Invalid `codex` config in {config_path}; expected a table.")

    extra_args_value = codex_cfg.get("extra_args")
    if extra_args_value is None:
        extra_args = ["-c", "notify=[]"]
    elif isinstance(extra_args_value, list) and all(
        isinstance(item, str) for item in extra_args_value
    ):
        extra_args = list(extra_args_value)
    else:
        raise ConfigError(
            f"Invalid `codex.extra_args` in {config_path}; expected a list of strings."
        )

    profile_value = profile or codex_cfg.get("profile")
    if profile_value:
        if not isinstance(profile_value, str):
            raise ConfigError(
                f"Invalid `codex.profile` in {config_path}; expected a string."
            )
        extra_args.extend(["--profile", profile_value])

    startup_msg = f"codex is ready\npwd: {startup_pwd}"

    bot = TelegramClient(token)
    runner = CodexRunner(codex_cmd=codex_cmd, extra_args=extra_args)

    return BridgeConfig(
        bot=bot,
        runner=runner,
        chat_id=chat_id,
        final_notify=final_notify,
        startup_msg=startup_msg,
        max_concurrency=16,
    )


async def _send_startup(cfg: BridgeConfig) -> None:
    logger.debug("[startup] message: %s", cfg.startup_msg)
    sent = await cfg.bot.send_message(chat_id=cfg.chat_id, text=cfg.startup_msg)
    if sent is not None:
        logger.info("[startup] sent startup message to chat_id=%s", cfg.chat_id)


async def _drain_backlog(cfg: BridgeConfig, offset: int | None) -> int | None:
    drained = 0
    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=0, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("[startup] backlog drain failed")
            return offset
        logger.debug("[startup] backlog updates: %s", updates)
        if not updates:
            if drained:
                logger.info("[startup] drained %s pending update(s)", drained)
            return offset
        offset = updates[-1]["update_id"] + 1
        drained += len(updates)


async def handle_message(
    cfg: BridgeConfig,
    *,
    chat_id: int,
    user_msg_id: int,
    text: str,
    resume_token: str | None,
    running_tasks: dict[int, RunningTask] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    progress_edit_every: float = PROGRESS_EDIT_EVERY_S,
) -> None:
    logger.debug(
        "[handle] incoming chat_id=%s message_id=%s resume=%r text=%s",
        chat_id,
        user_msg_id,
        resume_token,
        text,
    )
    started_at = clock()
    progress_renderer = ExecProgressRenderer(max_actions=5)

    progress_id: int | None = None
    last_edit_at = 0.0
    last_rendered: str | None = None

    initial_md = progress_renderer.render_progress(
        0.0, label=f"working ({cfg.runner.engine})"
    )
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
    if progress_msg is not None:
        progress_id = int(progress_msg["message_id"])
        last_edit_at = clock()
        last_rendered = initial_rendered
        logger.debug("[progress] sent chat_id=%s message_id=%s", chat_id, progress_id)

    edits = ProgressEdits(
        bot=cfg.bot,
        chat_id=chat_id,
        progress_id=progress_id,
        renderer=progress_renderer,
        started_at=started_at,
        progress_edit_every=progress_edit_every,
        clock=clock,
        sleep=sleep,
        limit=TELEGRAM_MARKDOWN_LIMIT,
        last_edit_at=last_edit_at,
        last_rendered=last_rendered,
    )

    exec_scope = anyio.CancelScope()
    cancel_exc_type = anyio.get_cancelled_exc_class()
    cancelled = False
    error: Exception | None = None
    resume_token_value: ResumeToken | None = None
    answer: str = ""
    saw_agent_message: bool = False
    running_task: RunningTask | None = None
    if running_tasks is not None and progress_id is not None:
        running_task = RunningTask(scope=exec_scope)
        running_tasks[progress_id] = running_task

    async def on_event(evt: TakopiEvent) -> None:
        await edits.on_event(evt)

    async with anyio.create_task_group() as tg:
        if progress_id is not None:
            tg.start_soon(edits.run)

        try:
            with exec_scope:
                resume_token_value, answer, saw_agent_message = await cfg.runner.run(
                    text, resume_token, on_event=on_event
                )
        except cancel_exc_type:
            cancelled = True
            resume_token_value = progress_renderer.resume_token
        except Exception as e:
            error = e
        finally:
            if (
                running_task is not None
                and running_tasks is not None
                and progress_id is not None
            ):
                running_tasks.pop(progress_id, None)
            if exec_scope.cancelled_caught and not cancelled and error is None:
                cancelled = True
                resume_token_value = progress_renderer.resume_token
            if not cancelled and error is None:
                await anyio.sleep(0)
            tg.cancel_scope.cancel()

    if error is not None:
        elapsed = clock() - started_at
        if resume_token_value is None:
            resume_token_value = progress_renderer.resume_token
        progress_renderer.resume_token = resume_token_value
        err_body = f"Error:\n{error}"
        final_md = progress_renderer.render_final(elapsed, err_body, status="error")
        logger.debug("[error] markdown: %s", final_md)
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

    elapsed = clock() - started_at
    if cancelled:
        logger.info(
            "[handle] cancelled resume=%s elapsed=%.1fs",
            resume_token_value.value if resume_token_value else None,
            elapsed,
        )
        progress_renderer.resume_token = resume_token_value
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

    if resume_token_value is None:
        raise RuntimeError("codex exec finished without a result")

    status = "done" if saw_agent_message else "error"
    progress_renderer.resume_token = resume_token_value
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

    final_msg, edited = await _send_or_edit_markdown(
        cfg.bot,
        chat_id=chat_id,
        text=final_md,
        edit_message_id=edit_message_id,
        reply_to_message_id=user_msg_id,
        disable_notification=False,
        limit=TELEGRAM_MARKDOWN_LIMIT,
    )
    if final_msg is None:
        return
    if progress_id is not None and (edit_message_id is None or not edited):
        logger.debug("[final] delete progress message_id=%s", progress_id)
        await cfg.bot.delete_message(chat_id=chat_id, message_id=progress_id)


async def poll_updates(cfg: BridgeConfig):
    offset: int | None = None
    offset = await _drain_backlog(cfg, offset)
    await _send_startup(cfg)

    while True:
        updates = await cfg.bot.get_updates(
            offset=offset, timeout_s=50, allowed_updates=["message"]
        )
        if updates is None:
            logger.info("[loop] getUpdates failed")
            await anyio.sleep(2)
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
    running_tasks: dict[int, RunningTask],
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

    progress_id = reply.get("message_id")
    if progress_id is None:
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="nothing is currently running for that message.",
            reply_to_message_id=user_msg_id,
        )
        return

    running_task = running_tasks.get(int(progress_id))
    if running_task is None:
        await cfg.bot.send_message(
            chat_id=chat_id,
            text="nothing is currently running for that message.",
            reply_to_message_id=user_msg_id,
        )
        return

    logger.info("[cancel] cancelling progress_message_id=%s", progress_id)
    running_task.scope.cancel()


async def _run_main_loop(cfg: BridgeConfig) -> None:
    worker_count = max(1, min(cfg.max_concurrency, 16))
    send_stream, receive_stream = anyio.create_memory_object_stream(
        max_buffer_size=worker_count * 2
    )
    running_tasks: dict[int, RunningTask] = {}

    async def worker() -> None:
        while True:
            try:
                (
                    chat_id,
                    user_msg_id,
                    text,
                    resume_token,
                ) = await receive_stream.receive()
            except anyio.EndOfStream:
                return
            try:
                await handle_message(
                    cfg,
                    chat_id=chat_id,
                    user_msg_id=user_msg_id,
                    text=text,
                    resume_token=resume_token,
                    running_tasks=running_tasks,
                )
            except Exception:
                logger.exception("[handle] worker failed")

    try:
        async with anyio.create_task_group() as tg:
            for _ in range(worker_count):
                tg.start_soon(worker)
            async for msg in poll_updates(cfg):
                text = msg["text"]
                user_msg_id = msg["message_id"]

                if text == "/cancel":
                    tg.start_soon(_handle_cancel, cfg, msg, running_tasks)
                    continue

                r = msg.get("reply_to_message") or {}
                resume_token = resolve_resume_token(text, r.get("text"))

                await send_stream.send(
                    (msg["chat"]["id"], user_msg_id, text, resume_token)
                )
    finally:
        await send_stream.aclose()
        await receive_stream.aclose()
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
    anyio.run(_run_main_loop, cfg)


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
