from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import logging
import sys
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from collections.abc import Callable

import typer

from .config import load_telegram_config
from .constants import TELEGRAM_HARD_LIMIT
from .exec_render import ExecProgressRenderer, render_event_cli
from .rendering import render_markdown
from .telegram_client import TelegramClient

# -------------------- Codex runner --------------------

logger = logging.getLogger("exec_bridge")


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
ELLIPSIS = "…"


def _clamp_tg_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...(truncated)"


def _send_markdown(
    bot: TelegramClient,
    *,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    rendered_text, entities = render_markdown(text)
    if len(rendered_text) > TELEGRAM_MARKDOWN_LIMIT:
        sep = "\n" + ELLIPSIS + "\n"
        lines = rendered_text.splitlines()
        tail = lines[-1] if lines else ""
        max_head = max(0, TELEGRAM_MARKDOWN_LIMIT - len(sep) - len(tail))
        rendered_text = rendered_text[:max_head] + sep + tail
        entities = None
    return bot.send_message(
        chat_id=chat_id,
        text=rendered_text,
        entities=entities or None,
        reply_to_message_id=reply_to_message_id,
        disable_notification=disable_notification,
    )


class ProgressEditor:
    def __init__(
        self,
        bot: TelegramClient,
        chat_id: int,
        message_id: int,
        edit_every_s: float,
        initial_text: str | None = None,
        initial_entities: list[dict[str, Any]] | None = None,
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.edit_every_s = edit_every_s

        self._lock = threading.Lock()
        self._pending: tuple[str, list[dict[str, Any]] | None] | None = None
        self._last_sent: tuple[str, list[dict[str, Any]] | None] | None = None
        self._last_edit_at = 0.0

        if initial_text is not None:
            self._last_sent = (initial_text, initial_entities)
            self._last_edit_at = time.monotonic()

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set(self, text: str, entities: list[dict[str, Any]] | None = None) -> None:
        text = _clamp_tg_text(text)
        with self._lock:
            self._pending = (text, entities)
        logger.debug(
            "[progress] set pending len=%s entities=%s", len(text), bool(entities)
        )

    def set_markdown(self, text: str) -> None:
        rendered_text, entities = render_markdown(text)
        self.set(rendered_text, entities or None)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _edit(self, text: str, entities: list[dict[str, Any]] | None) -> None:
        try:
            self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
                entities=entities,
            )
            logger.debug(
                "[progress] edit ok chat_id=%s message_id=%s len=%s",
                self.chat_id,
                self.message_id,
                len(text),
            )
        except Exception as e:
            logger.info(
                "[progress] edit failed chat_id=%s message_id=%s: %s",
                self.chat_id,
                self.message_id,
                e,
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            to_send: tuple[str, list[dict[str, Any]] | None] | None = None
            now = time.monotonic()
            with self._lock:
                if (
                    self._pending is not None
                    and (now - self._last_edit_at) >= self.edit_every_s
                ):
                    if self._pending != self._last_sent:
                        to_send = self._pending
                        self._last_sent = self._pending
                        self._last_edit_at = now
                    self._pending = None

            if to_send is not None:
                self._edit(to_send[0], to_send[1])

            self._stop.wait(0.2)


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

        # per-session locks to prevent concurrent resumes to same session_id
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            if session_id not in self._locks:
                self._locks[session_id] = threading.Lock()
            return self._locks[session_id]

    def run(
        self,
        prompt: str,
        session_id: str | None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[str, str, bool]:
        """
        Returns (session_id, final_agent_message_text)
        """
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

        # read both stdout+stderr without deadlock
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        logger.debug("[codex] spawn pid=%s args=%r", proc.pid, args)
        assert proc.stdin and proc.stdout and proc.stderr

        # send prompt then close stdin
        proc.stdin.write(prompt)
        proc.stdin.close()

        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            for line in proc.stderr:
                logger.info("[codex][stderr] %s", line.rstrip())
                stderr_lines.append(line)

        t = threading.Thread(target=_drain_stderr, daemon=True)
        t.start()

        found_session: str | None = session_id
        last_agent_text: str | None = None
        saw_agent_message = False

        cli_last_turn = None

        for line in proc.stdout:
            line = line.strip()
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
                    on_event(evt)
                except Exception as e:
                    logger.info("[codex][on_event] callback error: %s", e)

            # From Codex JSONL event stream
            if evt.get("type") == "thread.started":
                found_session = evt.get("thread_id") or found_session

            if evt.get("type") == "item.completed":
                item = evt.get("item") or {}
                if item.get("type") == "agent_message" and isinstance(
                    item.get("text"), str
                ):
                    last_agent_text = item["text"]
                    saw_agent_message = True

        rc = proc.wait()
        logger.debug("[codex] process exit pid=%s rc=%s", proc.pid, rc)
        t.join(timeout=2.0)

        if rc != 0:
            tail = "".join(stderr_lines[-200:])
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

    def run_serialized(
        self,
        prompt: str,
        session_id: str | None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[str, str, bool]:
        """
        If resuming, serialize per-session.
        """
        if not session_id:
            return self.run(prompt, session_id=None, on_event=on_event)
        lock = self._lock_for(session_id)
        with lock:
            return self.run(prompt, session_id=session_id, on_event=on_event)


# -------------------- Telegram loop --------------------


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
    workdir: str | None = typer.Option(
        None,
        "--workdir",
        help="Override codex workspace (--cd) for this exec-bridge run.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Codex model to pass to `codex exec`.",
    ),
) -> None:
    setup_logging(log_file if log_file else None)
    config = load_telegram_config()
    token = config["bot_token"]
    config_chat_id = int(config["chat_id"])

    startup_pwd = os.getcwd()
    startup_msg = f"codex exec bridge has started\npwd: {startup_pwd}"

    codex_cmd = shutil.which("codex")
    if not codex_cmd:
        raise RuntimeError("codex not found on PATH")
    workspace = workdir if workdir is not None else config.get("codex_workspace")
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
                if i + 1 >= len(args):
                    continue
                key = args[i + 1].split("=", 1)[0].strip()
                if key == "notify" or key.endswith(".notify"):
                    return True
            elif arg.startswith(("--config=", "-c=")):
                key = arg.split("=", 1)[1].split("=", 1)[0].strip()
                if key == "notify" or key.endswith(".notify"):
                    return True
        return False

    # Default: disable notify hook for exec-bridge runs to avoid duplicate messages.
    if not _has_notify_override(extra_args):
        extra_args.extend(["-c", "notify=[]"])

    bot = TelegramClient(token)
    runner = CodexExecRunner(
        codex_cmd=codex_cmd, workspace=workspace, extra_args=extra_args
    )

    max_workers = config.get("max_workers")
    pool = ThreadPoolExecutor(max_workers=max_workers or 4)
    offset: int | None = None
    ignore_backlog = bool(ignore_backlog)

    if ignore_backlog:
        try:
            updates = bot.get_updates(
                offset=offset, timeout_s=0, allowed_updates=["message"]
            )
        except Exception as e:
            logger.info("[startup] backlog drain failed: %s", e)
            updates = []
        if updates:
            offset = updates[-1]["update_id"] + 1
            logger.info("[startup] drained %s pending update(s)", len(updates))

    logger.info("[startup] pwd=%s", startup_pwd)
    logger.info("Option1 bridge running (codex exec). Long-polling Telegram...")
    try:
        bot.send_message(chat_id=config_chat_id, text=startup_msg)
        logger.info("[startup] sent startup message to chat_id=%s", config_chat_id)
    except Exception as e:
        logger.info("[startup] failed to send startup message to chat_id=%s: %s", config_chat_id, e)

    def handle(
        chat_id: int, user_msg_id: int, text: str, resume_session: str | None
    ) -> None:
        logger.info(
            "[handle] start chat_id=%s user_msg_id=%s resume_session=%r",
            chat_id,
            user_msg_id,
            resume_session,
        )
        logger.debug(
            "[handle] thread name=%s ident=%s",
            threading.current_thread().name,
            threading.get_ident(),
        )
        edit_every_s = progress_edit_every_s
        silent_progress = progress_silent
        loud_final = final_notify

        started_at = time.monotonic()
        session_box: dict[str, str | None] = {"id": resume_session}
        progress_renderer = ExecProgressRenderer(max_actions=5)

        progress_id: int | None = None
        progress: ProgressEditor | None = None
        try:
            initial_text = progress_renderer.render_progress(0.0)
            initial_rendered, initial_entities = render_markdown(initial_text)
            progress_msg = bot.send_message(
                chat_id=chat_id,
                text=initial_rendered,
                entities=initial_entities or None,
                reply_to_message_id=user_msg_id,
                disable_notification=silent_progress,
            )
            progress_id = int(progress_msg["message_id"])
            logger.debug(
                "[progress] sent chat_id=%s message_id=%s", chat_id, progress_id
            )
        except Exception as e:
            logger.info(
                "[handle] failed to send progress message chat_id=%s: %s", chat_id, e
            )

        if progress_id is not None:
            progress = ProgressEditor(
                bot,
                chat_id,
                progress_id,
                edit_every_s,
                initial_text=initial_rendered,
                initial_entities=initial_entities or None,
            )

        def on_event(evt: dict[str, Any]) -> None:
            event_type = evt.get("type")
            item = evt.get("item") or {}
            logger.debug(
                "[codex] event type=%s item_id=%s item_type=%s status=%s",
                event_type,
                item.get("id"),
                item.get("type"),
                item.get("status"),
            )
            if event_type == "thread.started":
                thread_id = evt.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    session_box["id"] = thread_id
            if progress_renderer.note_event(evt) and progress is not None:
                elapsed = time.monotonic() - started_at
                msg = progress_renderer.render_progress(elapsed)
                progress.set_markdown(msg)

        def _stop_background() -> None:
            if progress is not None:
                progress.stop()
                logger.debug("[progress] thread stopped")

        try:
            session_id, answer, saw_agent_message = runner.run_serialized(
                text,
                resume_session,
                on_event=on_event,
            )
        except Exception as e:
            _stop_background()
            err = _clamp_tg_text(f"Error:\n{e}")
            if progress_id is not None and len(err) <= TELEGRAM_TEXT_LIMIT:
                try:
                    bot.edit_message_text(
                        chat_id=chat_id, message_id=progress_id, text=err
                    )
                    logger.info(
                        "[handle] error chat_id=%s user_msg_id=%s resume_session=%r err=%s",
                        chat_id,
                        user_msg_id,
                        resume_session,
                        e,
                    )
                    return
                except Exception as ee:
                    logger.info("[handle] failed to edit progress into error: %s", ee)

            _send_markdown(
                bot, chat_id=chat_id, text=err, reply_to_message_id=user_msg_id
            )
            logger.info(
                "[handle] error chat_id=%s user_msg_id=%s resume_session=%r err=%s",
                chat_id,
                user_msg_id,
                resume_session,
                e,
            )
            return

        _stop_background()

        answer = answer or "(No agent_message captured from JSON stream.)"
        elapsed = time.monotonic() - started_at
        status = "done" if saw_agent_message else "error"
        final_md = progress_renderer.render_final(elapsed, answer, status=status)
        final_md = final_md + f"\n\nresume: `{session_id}`"
        final_text, final_entities = render_markdown(final_md)
        can_edit_final = (
            progress_id is not None and len(final_text) <= TELEGRAM_TEXT_LIMIT
        )

        if loud_final or not can_edit_final:
            _send_markdown(
                bot, chat_id=chat_id, text=final_md, reply_to_message_id=user_msg_id
            )
            if progress_id is not None:
                try:
                    bot.delete_message(chat_id=chat_id, message_id=progress_id)
                except Exception as e:
                    logger.info(
                        "[handle] delete progress failed chat_id=%s message_id=%s: %s",
                        chat_id,
                        progress_id,
                        e,
                    )
        else:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_id,
                text=final_text,
                entities=final_entities or None,
            )

        logger.info(
            "[handle] done chat_id=%s user_msg_id=%s session_id=%r",
            chat_id,
            user_msg_id,
            session_id,
        )

    while True:
        try:
            updates = bot.get_updates(
                offset=offset, timeout_s=50, allowed_updates=["message"]
            )
        except Exception as e:
            logger.info("[telegram] get_updates error: %s", e)
            time.sleep(2.0)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat_id = msg.get("chat", {}).get("id")
            from_bot = msg.get("from", {}).get("is_bot")
            msg_text = msg.get("text")
            reply_to = (msg.get("reply_to_message") or {}).get("message_id")
            logger.info(
                "[telegram] received update_id=%s chat_id=%s from_bot=%s has_text=%s reply_to=%s text=%s",
                upd.get("update_id"),
                chat_id,
                from_bot,
                msg_text is not None,
                reply_to,
                repr(msg_text),
            )
            if "text" not in msg:
                logger.info(
                    "[telegram] ignoring non-text message chat_id=%s update_id=%s",
                    chat_id,
                    upd.get("update_id"),
                )
                continue

            if int(chat_id) != config_chat_id:
                logger.info(
                    "[telegram] rejected by ACL chat_id=%s allowed=%s",
                    chat_id,
                    config_chat_id,
                )
                continue

            if msg.get("from", {}).get("is_bot"):
                logger.info(
                    "[telegram] ignoring bot message chat_id=%s update_id=%s",
                    chat_id,
                    upd.get("update_id"),
                )
                continue

            text = msg["text"]
            user_msg_id = msg["message_id"]
            logger.info(
                "[telegram] accepted message chat_id=%s user_msg_id=%s text=%s",
                chat_id,
                user_msg_id,
                repr(text),
            )

            uuid_re = re.compile(
                r"(?i)\\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\b"
            )

            def _extract_session_id(value: str | None) -> str | None:
                if not value:
                    return None
                m = uuid_re.search(value)
                return m.group(0) if m else None

            resume_session = _extract_session_id(text)
            r = msg.get("reply_to_message") or {}
            resume_session = resume_session or _extract_session_id(r.get("text"))

            pool.submit(handle, chat_id, user_msg_id, text, resume_session)


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
