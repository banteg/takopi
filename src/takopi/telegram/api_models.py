from __future__ import annotations

from typing import Any

import msgspec

__all__ = [
    "Chat",
    "ChatMember",
    "File",
    "ForumTopic",
    "Message",
    "Update",
    "User",
]


class User(msgspec.Struct, forbid_unknown_fields=False):
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class Chat(msgspec.Struct, forbid_unknown_fields=False):
    id: int
    type: str
    is_forum: bool | None = None


class ChatMember(msgspec.Struct, forbid_unknown_fields=False):
    status: str
    can_manage_topics: bool | None = None


class Message(msgspec.Struct, forbid_unknown_fields=False):
    message_id: int
    message_thread_id: int | None = None
    text: str | None = None


class File(msgspec.Struct, forbid_unknown_fields=False):
    file_path: str


class ForumTopic(msgspec.Struct, forbid_unknown_fields=False):
    message_thread_id: int


class Update(msgspec.Struct, forbid_unknown_fields=False):
    update_id: int
    message: dict[str, Any] | None = None
    callback_query: dict[str, Any] | None = None
