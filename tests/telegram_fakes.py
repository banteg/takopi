from typing import Any

import anyio

from takopi.config import ProjectsConfig
from takopi.markdown import MarkdownPresenter
from takopi.router import AutoRouter, RunnerEntry
from takopi.runner_bridge import ExecBridgeConfig
from takopi.runners.mock import Return, ScriptRunner
from takopi.telegram.api_models import (
    Chat,
    ChatMember,
    File,
    ForumTopic,
    Message,
    Update,
    User,
)
from takopi.telegram.bridge import TelegramBridgeConfig
from takopi.telegram.client import BotClient
from takopi.transport import MessageRef, RenderedMessage, SendOptions
from takopi.transport_runtime import TransportRuntime

DEFAULT_ENGINE_ID = "codex"


def _empty_projects() -> ProjectsConfig:
    return ProjectsConfig(projects={}, default_project=None)


def _make_router(runner: Any) -> AutoRouter:
    return AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )


class FakeTransport:
    def __init__(self, progress_ready: anyio.Event | None = None) -> None:
        self._next_id = 1
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.delete_calls: list[MessageRef] = []
        self.progress_ready = progress_ready
        self.progress_ref: MessageRef | None = None

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        ref = MessageRef(channel_id=channel_id, message_id=self._next_id)
        self._next_id += 1
        self.send_calls.append(
            {
                "ref": ref,
                "channel_id": channel_id,
                "message": message,
                "options": options,
            }
        )
        if (
            self.progress_ref is None
            and options is not None
            and options.reply_to is not None
            and options.notify is False
        ):
            self.progress_ref = ref
            if self.progress_ready is not None:
                self.progress_ready.set()
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        self.edit_calls.append({"ref": ref, "message": message, "wait": wait})
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        self.delete_calls.append(ref)
        return True

    async def close(self) -> None:
        return None


class FakeBot(BotClient):
    def __init__(self) -> None:
        self.command_calls: list[dict] = []
        self.callback_calls: list[dict] = []
        self.send_calls: list[dict] = []
        self.document_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.edit_topic_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict] = []

    async def get_updates(
        self,
        offset: int | None,
        timeout_s: int = 50,
        allowed_updates: list[str] | None = None,
    ) -> list[Update] | None:
        _ = offset
        _ = timeout_s
        _ = allowed_updates
        return []

    async def get_file(self, file_id: str) -> File | None:
        _ = file_id
        return None

    async def download_file(self, file_path: str) -> bytes | None:
        _ = file_path
        return None

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        disable_notification: bool | None = False,
        message_thread_id: int | None = None,
        entities: list[dict[str, Any]] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        *,
        replace_message_id: int | None = None,
    ) -> Message:
        self.send_calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
                "disable_notification": disable_notification,
                "message_thread_id": message_thread_id,
                "entities": entities,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "replace_message_id": replace_message_id,
            }
        )
        return Message(message_id=1, chat=Chat(id=chat_id, type="private"))

    async def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
        disable_notification: bool | None = False,
        caption: str | None = None,
    ) -> Message:
        self.document_calls.append(
            {
                "chat_id": chat_id,
                "filename": filename,
                "content": content,
                "reply_to_message_id": reply_to_message_id,
                "message_thread_id": message_thread_id,
                "disable_notification": disable_notification,
                "caption": caption,
            }
        )
        return Message(message_id=2, chat=Chat(id=chat_id, type="private"))

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        entities: list[dict[str, Any]] | None = None,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        *,
        wait: bool = True,
    ) -> Message:
        self.edit_calls.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "entities": entities,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
                "wait": wait,
            }
        )
        return Message(message_id=message_id, chat=Chat(id=chat_id, type="private"))

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        self.delete_calls.append({"chat_id": chat_id, "message_id": message_id})
        return True

    async def set_my_commands(
        self,
        commands: list[dict[str, Any]],
        *,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> bool:
        self.command_calls.append(
            {
                "commands": commands,
                "scope": scope,
                "language_code": language_code,
            }
        )
        return True

    async def get_me(self) -> User | None:
        return User(id=1, username="bot")

    async def get_chat(self, chat_id: int) -> Chat | None:
        _ = chat_id
        return Chat(id=chat_id, type="supergroup", is_forum=True)

    async def get_chat_member(self, chat_id: int, user_id: int) -> ChatMember | None:
        _ = chat_id
        _ = user_id
        return ChatMember(status="administrator", can_manage_topics=True)

    async def create_forum_topic(self, chat_id: int, name: str) -> ForumTopic | None:
        _ = chat_id
        _ = name
        return ForumTopic(message_thread_id=1)

    async def edit_forum_topic(
        self, chat_id: int, message_thread_id: int, name: str
    ) -> bool:
        self.edit_topic_calls.append(
            {
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "name": name,
            }
        )
        return True

    async def close(self) -> None:
        return None

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool:
        self.callback_calls.append(
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
        )
        return True


def make_cfg(
    transport: FakeTransport,
    runner: ScriptRunner | None = None,
    *,
    engine_id: str = DEFAULT_ENGINE_ID,
    forward_coalesce_s: float = 0.0,
    media_group_debounce_s: float = 0.0,
) -> TelegramBridgeConfig:
    if runner is None:
        runner = ScriptRunner([Return(answer="ok")], engine=engine_id)
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=MarkdownPresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    return TelegramBridgeConfig(
        bot=FakeBot(),
        runtime=runtime,
        chat_id=123,
        startup_msg="",
        exec_cfg=exec_cfg,
        forward_coalesce_s=forward_coalesce_s,
        media_group_debounce_s=media_group_debounce_s,
    )
