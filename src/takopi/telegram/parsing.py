from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

import anyio
import msgspec

from ..logging import get_logger
from .api_schemas import (
    CallbackQuery,
    Document,
    Message,
    PhotoSize,
    Sticker,
    Update,
    Video,
)
from .client_api import BotClient
from .types import (
    TelegramCallbackQuery,
    TelegramDocument,
    TelegramIncomingMessage,
    TelegramIncomingUpdate,
    TelegramVoice,
)

logger = get_logger(__name__)


def parse_incoming_update(
    update: Update,
    *,
    chat_id: int | None = None,
    chat_ids: set[int] | None = None,
) -> TelegramIncomingUpdate | None:
    if update.message is not None:
        return _parse_incoming_message(
            update.message,
            chat_id=chat_id,
            chat_ids=chat_ids,
        )
    if update.callback_query is not None:
        return _parse_callback_query(
            update.callback_query,
            chat_id=chat_id,
            chat_ids=chat_ids,
        )
    return None


def _parse_incoming_message(
    msg: Message,
    *,
    chat_id: int | None = None,
    chat_ids: set[int] | None = None,
) -> TelegramIncomingMessage | None:
    raw_text = msg.text
    caption = msg.caption
    text = raw_text if raw_text is not None else caption
    if text is None:
        text = ""
    file_command = False
    stripped = text.lstrip()
    if stripped.startswith("/"):
        token = stripped.split(maxsplit=1)[0]
        file_command = token.startswith("/file")
    voice_payload: TelegramVoice | None = None
    if msg.voice is not None:
        voice_payload = TelegramVoice(
            file_id=msg.voice.file_id,
            mime_type=msg.voice.mime_type,
            file_size=msg.voice.file_size,
            duration=msg.voice.duration,
            raw=msgspec.to_builtins(msg.voice),
        )
        if raw_text is None and caption is None:
            text = ""
    document_payload: TelegramDocument | None = None
    if msg.document is not None:
        document_payload = _document_from_media(msg.document)
    if document_payload is None and msg.video is not None:
        document_payload = _document_from_media(msg.video)
    if document_payload is None:
        best = _best_photo(msg.photo)
        if best is not None:
            document_payload = _document_from_photo(best)
    if document_payload is None and file_command and msg.sticker is not None:
        document_payload = _document_from_sticker(msg.sticker)
    has_text = raw_text is not None or caption is not None
    if not has_text and voice_payload is None and document_payload is None:
        return None
    msg_chat_id = msg.chat.id
    chat_type = msg.chat.type
    is_forum = msg.chat.is_forum
    allowed = chat_ids
    if allowed is None and chat_id is not None:
        allowed = {chat_id}
    if allowed is not None and msg_chat_id not in allowed:
        return None
    reply = msg.reply_to_message
    reply_to_message_id = reply.message_id if reply is not None else None
    reply_to_text = reply.text if reply is not None else None
    reply_to_is_bot = (
        reply.from_.is_bot if reply is not None and reply.from_ is not None else None
    )
    reply_to_username = (
        reply.from_.username if reply is not None and reply.from_ is not None else None
    )
    sender_id = msg.from_.id if msg.from_ is not None else None
    media_group_id = msg.media_group_id
    thread_id = msg.message_thread_id
    is_topic_message = msg.is_topic_message
    if thread_id is not None and reply_to_message_id == thread_id:
        reply_to_message_id = None
        reply_to_text = None
        reply_to_is_bot = None
        reply_to_username = None
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=msg_chat_id,
        message_id=msg.message_id,
        text=text,
        reply_to_message_id=reply_to_message_id,
        reply_to_text=reply_to_text,
        reply_to_is_bot=reply_to_is_bot,
        reply_to_username=reply_to_username,
        sender_id=sender_id,
        media_group_id=media_group_id,
        thread_id=thread_id,
        is_topic_message=is_topic_message,
        chat_type=chat_type,
        is_forum=is_forum,
        voice=voice_payload,
        document=document_payload,
        raw=msgspec.to_builtins(msg),
    )


def _parse_callback_query(
    query: CallbackQuery,
    *,
    chat_id: int | None = None,
    chat_ids: set[int] | None = None,
) -> TelegramCallbackQuery | None:
    callback_id = query.id
    msg = query.message
    if msg is None:
        return None
    msg_chat_id = msg.chat.id
    allowed = chat_ids
    if allowed is None and chat_id is not None:
        allowed = {chat_id}
    if allowed is not None and msg_chat_id not in allowed:
        return None
    data = query.data
    sender_id = query.from_.id if query.from_ is not None else None
    return TelegramCallbackQuery(
        transport="telegram",
        chat_id=msg_chat_id,
        message_id=msg.message_id,
        callback_query_id=callback_id,
        data=data,
        sender_id=sender_id,
        raw=msgspec.to_builtins(query),
    )


def _best_photo(photos: list[PhotoSize] | None) -> PhotoSize | None:
    if not photos:
        return None
    best = None
    best_score = -1
    for item in photos:
        size = item.file_size
        score = size if size is not None else item.width * item.height
        if score > best_score:
            best_score = score
            best = item
    return best


def _document_from_media(media: Document | Video) -> TelegramDocument:
    return TelegramDocument(
        file_id=media.file_id,
        file_name=media.file_name,
        mime_type=media.mime_type,
        file_size=media.file_size,
        raw=msgspec.to_builtins(media),
    )


def _document_from_photo(photo: PhotoSize) -> TelegramDocument:
    return TelegramDocument(
        file_id=photo.file_id,
        file_name=None,
        mime_type=None,
        file_size=photo.file_size,
        raw=msgspec.to_builtins(photo),
    )


def _document_from_sticker(sticker: Sticker) -> TelegramDocument:
    return TelegramDocument(
        file_id=sticker.file_id,
        file_name=None,
        mime_type=None,
        file_size=sticker.file_size,
        raw=msgspec.to_builtins(sticker),
    )


async def poll_incoming(
    bot: BotClient,
    *,
    chat_id: int | None = None,
    chat_ids: Iterable[int] | Callable[[], Iterable[int]] | None = None,
    offset: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
) -> AsyncIterator[TelegramIncomingUpdate]:
    while True:
        updates = await bot.get_updates(
            offset=offset,
            timeout_s=50,
            allowed_updates=["message", "callback_query"],
        )
        if updates is None:
            logger.info("loop.get_updates.failed")
            await sleep(2)
            continue
        logger.debug("loop.updates", updates=updates)
        resolved_chat_ids = chat_ids() if callable(chat_ids) else chat_ids
        allowed = set(resolved_chat_ids) if resolved_chat_ids is not None else None
        if allowed is None and chat_id is not None:
            allowed = {chat_id}
        for upd in updates:
            offset = upd.update_id + 1
            msg = parse_incoming_update(upd, chat_ids=allowed)
            if msg is not None:
                yield msg
