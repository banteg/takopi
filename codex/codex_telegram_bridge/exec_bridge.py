from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

from bridge_common import TelegramClient, RouteStore, parse_allowed_chat_ids

# -------------------- Codex runner --------------------


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _one_line(text: Optional[str]) -> str:
    if text is None:
        return "None"
    return text.replace("\r", "\\r").replace("\n", "\\n")


class CodexExecRunner:
    """
    Runs Codex in non-interactive mode:
      - new:    codex exec --json ... -
      - resume: codex exec --json ... resume <SESSION_ID> -
    """

    def __init__(self, codex_cmd: str, workspace: Optional[str], extra_args: list[str]) -> None:
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

    def run(self, prompt: str, session_id: Optional[str]) -> Tuple[str, str]:
        """
        Returns (session_id, final_agent_message_text)
        """
        log(f"[codex] start run session_id={session_id!r} workspace={self.workspace!r}")
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
        assert proc.stdin and proc.stdout and proc.stderr

        # send prompt then close stdin
        proc.stdin.write(prompt)
        proc.stdin.close()

        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            for line in proc.stderr:
                log(f"[codex][stderr] {line.rstrip()}")
                stderr_lines.append(line)

        t = threading.Thread(target=_drain_stderr, daemon=True)
        t.start()

        found_session: Optional[str] = session_id
        last_agent_text: Optional[str] = None

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            log(f"[codex][event] {line}")
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            # From Codex JSONL event stream
            if evt.get("type") == "thread.started":
                found_session = evt.get("thread_id") or found_session

            if evt.get("type") == "item.completed":
                item = evt.get("item") or {}
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    last_agent_text = item["text"]

        rc = proc.wait()
        t.join(timeout=2.0)

        if rc != 0:
            tail = "".join(stderr_lines[-200:])
            raise RuntimeError(f"codex exec failed (rc={rc}). stderr tail:\n{tail}")

        if not found_session:
            raise RuntimeError("codex exec finished but no session_id/thread_id was captured")

        log(f"[codex] done run session_id={found_session!r}")
        return found_session, (last_agent_text or "(No agent_message captured from JSON stream.)")

    def run_serialized(self, prompt: str, session_id: Optional[str]) -> Tuple[str, str]:
        """
        If resuming, serialize per-session.
        """
        if not session_id:
            return self.run(prompt, session_id=None)
        lock = self._lock_for(session_id)
        with lock:
            return self.run(prompt, session_id=session_id)


# -------------------- Telegram loop --------------------


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    db_path = os.environ.get("BRIDGE_DB", "./bridge_routes.sqlite3")
    allowed = parse_allowed_chat_ids(os.environ.get("ALLOWED_CHAT_IDS", ""))
    startup_ids = parse_allowed_chat_ids(os.environ.get("STARTUP_CHAT_IDS", "")) or allowed
    startup_msg = os.environ.get("STARTUP_MESSAGE", "✅ exec_bridge started (codex exec).")
    startup_pwd = os.getcwd()
    startup_msg = f"{startup_msg}\nPWD: {startup_pwd}"

    codex_cmd = os.environ.get("CODEX_CMD", "codex")
    workspace = os.environ.get("CODEX_WORKSPACE")  # optional
    extra_args = shlex.split(os.environ.get("CODEX_EXEC_ARGS", ""))  # e.g. "--full-auto --search"

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
    store = RouteStore(db_path)
    runner = CodexExecRunner(codex_cmd=codex_cmd, workspace=workspace, extra_args=extra_args)

    pool = ThreadPoolExecutor(max_workers=int(os.environ.get("MAX_WORKERS", "4")))
    offset: Optional[int] = None

    log(f"[startup] pwd={startup_pwd}")
    log("Option1 bridge running (codex exec). Long-polling Telegram...")
    if startup_ids:
        for chat_id in startup_ids:
            try:
                bot.send_message(chat_id=chat_id, text=startup_msg)
                log(f"[startup] sent startup message to chat_id={chat_id}")
            except Exception as e:
                log(f"[startup] failed to send startup message to chat_id={chat_id}: {e}")
    else:
        log("[startup] no STARTUP_CHAT_IDS or ALLOWED_CHAT_IDS set; skipping startup message")

    def handle(chat_id: int, user_msg_id: int, text: str, resume_session: Optional[str]) -> None:
        log(
            "[handle] start "
            f"chat_id={chat_id} user_msg_id={user_msg_id} resume_session={resume_session!r}"
        )
        try:
            session_id, answer = runner.run_serialized(text, resume_session)
            sent_msgs = bot.send_message_chunked(
                chat_id=chat_id,
                text=answer,
                reply_to_message_id=user_msg_id,
            )
            for m in sent_msgs:
                store.link(chat_id, m["message_id"], "exec", session_id, meta={"workspace": workspace})
            log(
                "[handle] done "
                f"chat_id={chat_id} user_msg_id={user_msg_id} session_id={session_id!r}"
            )
        except Exception as e:
            err = f"❌ Error:\n{e}"
            sent_msgs = bot.send_message_chunked(chat_id=chat_id, text=err, reply_to_message_id=user_msg_id)
            for m in sent_msgs:
                store.link(chat_id, m["message_id"], "exec", resume_session or "unknown", meta={"error": True})
            log(
                "[handle] error "
                f"chat_id={chat_id} user_msg_id={user_msg_id} resume_session={resume_session!r} err={e}"
            )

    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout_s=50, allowed_updates=["message"])
        except Exception as e:
            log(f"[telegram] get_updates error: {e}")
            time.sleep(2.0)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat_id = msg.get("chat", {}).get("id")
            from_bot = msg.get("from", {}).get("is_bot")
            msg_text = msg.get("text")
            reply_to = (msg.get("reply_to_message") or {}).get("message_id")
            log(
                "[telegram] received "
                f"update_id={upd.get('update_id')} chat_id={chat_id} "
                f"from_bot={from_bot} has_text={msg_text is not None} "
                f"reply_to={reply_to} text={_one_line(msg_text)}"
            )
            if "text" not in msg:
                log(
                    "[telegram] ignoring non-text message "
                    f"chat_id={chat_id} update_id={upd.get('update_id')}"
                )
                continue

            if allowed is not None and int(chat_id) not in allowed:
                log(
                    "[telegram] rejected by ACL "
                    f"chat_id={chat_id} allowed={sorted(allowed)}"
                )
                continue

            if msg.get("from", {}).get("is_bot"):
                log(
                    "[telegram] ignoring bot message "
                    f"chat_id={chat_id} update_id={upd.get('update_id')}"
                )
                continue

            text = msg["text"]
            user_msg_id = msg["message_id"]
            log(
                "[telegram] accepted message "
                f"chat_id={chat_id} user_msg_id={user_msg_id} text={_one_line(text)}"
            )

            # If user replied to a bot message, route to that session
            resume_session: Optional[str] = None
            r = msg.get("reply_to_message")
            if r and "message_id" in r:
                route = store.resolve(chat_id, r["message_id"])
                if route and route.route_type == "exec":
                    resume_session = route.route_id
                    log(
                        "[telegram] resolved reply route "
                        f"chat_id={chat_id} bot_message_id={r['message_id']} session_id={resume_session!r}"
                    )
                else:
                    log(
                        "[telegram] reply has no exec route "
                        f"chat_id={chat_id} bot_message_id={r['message_id']}"
                    )

            pool.submit(handle, chat_id, user_msg_id, text, resume_session)


if __name__ == "__main__":
    main()
