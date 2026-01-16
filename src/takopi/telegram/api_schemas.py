"""Msgspec models for Telegram Bot API payloads (subset used by takopi).

Derived from telegram-api.html in the repository.
"""

from __future__ import annotations

import msgspec

__all__ = [
    "CallbackQuery",
    "CallbackQueryMessage",
    "Chat",
    "ChatMember",
    "Document",
    "File",
    "ForumTopic",
    "Message",
    "MessageReply",
    "PhotoSize",
    "Sticker",
    "Update",
    "User",
    "Video",
    "Voice",
    "decode_update",
    "decode_updates",
]


class User(msgspec.Struct, forbid_unknown_fields=False):
    id: int
    is_bot: bool | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class Chat(msgspec.Struct, forbid_unknown_fields=False):
    id: int
    type: str
    title: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    is_forum: bool | None = None


class PhotoSize(msgspec.Struct, forbid_unknown_fields=False):
    file_id: str
    width: int
    height: int
    file_size: int | None = None


class Document(msgspec.Struct, forbid_unknown_fields=False):
    file_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class Video(msgspec.Struct, forbid_unknown_fields=False):
    file_id: str
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None


class Voice(msgspec.Struct, forbid_unknown_fields=False):
    file_id: str
    duration: int | None = None
    mime_type: str | None = None
    file_size: int | None = None


class Sticker(msgspec.Struct, forbid_unknown_fields=False):
    file_id: str
    file_size: int | None = None


class MessageReply(msgspec.Struct, forbid_unknown_fields=False):
    message_id: int
    text: str | None = None
    from_: User | None = msgspec.field(default=None, name="from")


class Message(msgspec.Struct, forbid_unknown_fields=False):
    message_id: int
    chat: Chat
    message_thread_id: int | None = None
    from_: User | None = msgspec.field(default=None, name="from")
    text: str | None = None
    caption: str | None = None
    reply_to_message: MessageReply | None = None
    forward_from: User | None = None
    forward_from_chat: Chat | None = None
    forward_from_message_id: int | None = None
    forward_sender_name: str | None = None
    forward_signature: str | None = None
    forward_date: int | None = None
    media_group_id: str | None = None
    is_automatic_forward: bool | None = None
    is_topic_message: bool | None = None
    voice: Voice | None = None
    document: Document | None = None
    video: Video | None = None
    photo: list[PhotoSize] | None = None
    sticker: Sticker | None = None


class CallbackQueryMessage(msgspec.Struct, forbid_unknown_fields=False):
    message_id: int
    chat: Chat


class CallbackQuery(msgspec.Struct, forbid_unknown_fields=False):
    id: str
    from_: User = msgspec.field(name="from")
    message: CallbackQueryMessage | None = None
    data: str | None = None


class Update(msgspec.Struct, forbid_unknown_fields=False):
    update_id: int
    message: Message | None = None
    callback_query: CallbackQuery | None = None


class File(msgspec.Struct, forbid_unknown_fields=False):
    file_path: str


class ChatMember(msgspec.Struct, forbid_unknown_fields=False):
    status: str
    can_manage_topics: bool | None = None


class ForumTopic(msgspec.Struct, forbid_unknown_fields=False):
    message_thread_id: int


_UPDATE_DECODER = msgspec.json.Decoder(Update)
_UPDATES_DECODER = msgspec.json.Decoder(list[Update])


def decode_update(payload: str | bytes) -> Update:
    return _UPDATE_DECODER.decode(payload)


def decode_updates(payload: str | bytes) -> list[Update]:
    return _UPDATES_DECODER.decode(payload)
