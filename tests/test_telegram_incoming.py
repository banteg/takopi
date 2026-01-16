from takopi.telegram import (
    TelegramCallbackQuery,
    TelegramIncomingMessage,
    parse_incoming_update,
)
from takopi.telegram.api_models import (
    CallbackQuery,
    CallbackQueryMessage,
    Chat,
    Document,
    ForumTopicCreated,
    Message,
    MessageReply,
    PhotoSize,
    Sticker,
    Update,
    User,
    Video,
    Voice,
    decode_update,
)


def test_parse_incoming_update_maps_fields() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            text="hello",
            chat=Chat(id=123, type="supergroup", is_forum=True),
            from_=User(id=99),
            reply_to_message=MessageReply(
                message_id=5,
                text="prev",
                from_=User(id=77, is_bot=True, username="ReplyBot"),
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.transport == "telegram"
    assert msg.chat_id == 123
    assert msg.message_id == 10
    assert msg.text == "hello"
    assert msg.reply_to_message_id == 5
    assert msg.reply_to_text == "prev"
    assert msg.reply_to_is_bot is True
    assert msg.reply_to_username == "ReplyBot"
    assert msg.sender_id == 99
    assert msg.thread_id is None
    assert msg.is_topic_message is None
    assert msg.chat_type == "supergroup"
    assert msg.is_forum is True
    assert msg.voice is None
    assert msg.document is None
    assert msg.raw
    assert msg.raw["message_id"] == 10


def test_parse_incoming_update_filters_non_matching_chat() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            text="hello",
            chat=Chat(id=123, type="private"),
        ),
    )

    assert parse_incoming_update(update, chat_id=999) is None


def test_parse_incoming_update_filters_non_text_and_non_voice() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            chat=Chat(id=123, type="private"),
        ),
    )

    assert parse_incoming_update(update, chat_id=123) is None


def test_parse_incoming_update_voice_message() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            chat=Chat(id=123, type="private"),
            voice=Voice(
                file_id="voice-id",
                duration=3,
                mime_type="audio/ogg",
                file_size=1234,
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == ""
    assert msg.voice is not None
    assert msg.voice.file_id == "voice-id"
    assert msg.voice.mime_type == "audio/ogg"
    assert msg.voice.file_size == 1234
    assert msg.voice.duration == 3


def test_parse_incoming_update_document_message() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            caption="/file put incoming/doc.txt",
            chat=Chat(id=123, type="private"),
            document=Document(
                file_id="doc-id",
                file_name="doc.txt",
                mime_type="text/plain",
                file_size=4321,
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == "/file put incoming/doc.txt"
    assert msg.document is not None
    assert msg.document.file_id == "doc-id"
    assert msg.document.file_name == "doc.txt"
    assert msg.document.mime_type == "text/plain"
    assert msg.document.file_size == 4321


def test_parse_incoming_update_photo_message() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            caption="/file put incoming/photo.jpg",
            chat=Chat(id=123, type="private"),
            photo=[
                PhotoSize(
                    file_id="small",
                    file_size=100,
                    width=90,
                    height=90,
                ),
                PhotoSize(
                    file_id="large",
                    file_size=1000,
                    width=800,
                    height=600,
                ),
            ],
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == "/file put incoming/photo.jpg"
    assert msg.document is not None
    assert msg.document.file_id == "large"
    assert msg.document.file_name is None
    assert msg.document.file_size == 1000


def test_parse_incoming_update_media_group_id() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            chat=Chat(id=123, type="private"),
            media_group_id="group-1",
            photo=[
                PhotoSize(
                    file_id="large",
                    file_size=1000,
                    width=800,
                    height=600,
                )
            ],
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.media_group_id == "group-1"


def test_parse_incoming_update_video_message() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            caption="/file put incoming/video.mp4",
            chat=Chat(id=123, type="private"),
            video=Video(
                file_id="video-id",
                file_name="video.mp4",
                mime_type="video/mp4",
                file_size=4242,
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == "/file put incoming/video.mp4"
    assert msg.document is not None
    assert msg.document.file_id == "video-id"
    assert msg.document.file_name == "video.mp4"
    assert msg.document.mime_type == "video/mp4"
    assert msg.document.file_size == 4242


def test_parse_incoming_update_sticker_message() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            caption="/file put incoming/sticker.webp",
            chat=Chat(id=123, type="private"),
            sticker=Sticker(
                file_id="sticker-id",
                file_size=2468,
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == "/file put incoming/sticker.webp"
    assert msg.document is not None
    assert msg.document.file_id == "sticker-id"
    assert msg.document.file_name is None
    assert msg.document.mime_type is None
    assert msg.document.file_size == 2468


def test_parse_incoming_update_callback_query() -> None:
    update = Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="cbq-1",
            data="takopi:cancel",
            from_=User(id=321),
            message=CallbackQueryMessage(
                message_id=55,
                chat=Chat(id=123, type="private"),
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=123)
    assert isinstance(msg, TelegramCallbackQuery)
    assert msg.transport == "telegram"
    assert msg.chat_id == 123
    assert msg.message_id == 55
    assert msg.callback_query_id == "cbq-1"
    assert msg.data == "takopi:cancel"
    assert msg.sender_id == 321


def test_parse_incoming_update_topic_fields() -> None:
    update = Update(
        update_id=1,
        message=Message(
            message_id=10,
            text="hello",
            message_thread_id=77,
            is_topic_message=True,
            chat=Chat(id=-100, type="supergroup", is_forum=True),
        ),
    )

    msg = parse_incoming_update(update, chat_id=-100)
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.thread_id == 77
    assert msg.is_topic_message is True
    assert msg.chat_type == "supergroup"
    assert msg.is_forum is True


def test_reply_to_forum_topic_created_by_bot_ignores_is_bot() -> None:
    """Test that reply_to_is_bot is None when replying to a forum topic creation message.

    Observed behavior: Telegram sets reply_to_message to the topic creation message
    for messages sent in a topic without an explicit reply. When the bot creates
    a topic, this caused reply_to_is_bot to incorrectly be True for messages that
    weren't actually replies to the bot.

    This test reproduces the exact payload structure observed in production:
    - message_thread_id: 163 (the topic ID)
    - reply_to_message.message_id: 163 (same as thread ID - the topic creation)
    - reply_to_message.from.is_bot: True (bot created the topic)
    - reply_to_message.forum_topic_created: present (marks this as topic creation)
    """
    update = Update(
        update_id=1,
        message=Message(
            message_id=187,
            text="Hello",
            message_thread_id=163,
            is_topic_message=True,
            chat=Chat(id=-1001234567890, type="supergroup", is_forum=True),
            from_=User(id=12345, username="testuser"),
            reply_to_message=MessageReply(
                message_id=163,
                from_=User(id=8312076814, is_bot=True, username="TakopiBot"),
                forum_topic_created=ForumTopicCreated(
                    name="party-testing7 @main",
                    icon_color=7322096,
                ),
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=-1001234567890)
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == "Hello"
    assert msg.thread_id == 163
    assert msg.reply_to_message_id == 163
    # This is the key assertion: reply_to_is_bot should be None, not True
    # Even though the reply_to_message.from.is_bot is True, we ignore it
    # because it's a forum_topic_created message, not a real bot reply
    assert msg.reply_to_is_bot is None
    # Username should still be extracted
    assert msg.reply_to_username == "TakopiBot"


def test_reply_to_actual_bot_message_sets_is_bot() -> None:
    """Test that reply_to_is_bot is True when explicitly replying to a bot message.

    This ensures the fix doesn't break normal bot reply detection.
    When a user explicitly replies to a message from the bot (not a topic creation),
    reply_to_is_bot should still be True.
    """
    update = Update(
        update_id=1,
        message=Message(
            message_id=200,
            text="Thanks for the help!",
            message_thread_id=163,
            is_topic_message=True,
            chat=Chat(id=-1001234567890, type="supergroup", is_forum=True),
            from_=User(id=12345, username="testuser"),
            reply_to_message=MessageReply(
                message_id=195,  # Different from thread_id - explicit reply
                text="Here's the answer to your question...",
                from_=User(id=8312076814, is_bot=True, username="TakopiBot"),
                # No forum_topic_created - this is a normal message
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=-1001234567890)
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.reply_to_message_id == 195
    assert msg.reply_to_text == "Here's the answer to your question..."
    # This should be True - it's a real reply to a bot message
    assert msg.reply_to_is_bot is True
    assert msg.reply_to_username == "TakopiBot"


def test_reply_to_human_created_topic_sets_is_bot_false() -> None:
    """Test that reply_to_is_bot is None when topic was created by a human.

    When a human creates a topic manually (not via the bot), the topic creation
    message has from.is_bot=False. The fix should still work correctly here.
    """
    update = Update(
        update_id=1,
        message=Message(
            message_id=50,
            text="Hello everyone",
            message_thread_id=45,
            is_topic_message=True,
            chat=Chat(id=-1001234567890, type="supergroup", is_forum=True),
            from_=User(id=12345, username="testuser"),
            reply_to_message=MessageReply(
                message_id=45,
                from_=User(id=67890, is_bot=False, username="admin"),
                forum_topic_created=ForumTopicCreated(
                    name="General Discussion",
                    icon_color=16766590,
                ),
            ),
        ),
    )

    msg = parse_incoming_update(update, chat_id=-1001234567890)
    assert isinstance(msg, TelegramIncomingMessage)
    # reply_to_is_bot should be None because it's a topic creation message
    # (even though is_bot=False, we still skip it for consistency)
    assert msg.reply_to_is_bot is None
    assert msg.reply_to_username == "admin"


def test_message_in_general_topic_no_reply() -> None:
    """Test that messages in the General topic (no reply_to_message) work correctly.

    Messages in the General topic don't have reply_to_message set, so this
    case should not be affected by the fix.
    """
    update = Update(
        update_id=1,
        message=Message(
            message_id=186,
            text="Hello",
            chat=Chat(id=-1001234567890, type="supergroup", is_forum=True),
            from_=User(id=12345, username="testuser"),
            # No reply_to_message, no message_thread_id - General topic
        ),
    )

    msg = parse_incoming_update(update, chat_id=-1001234567890)
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == "Hello"
    assert msg.thread_id is None
    assert msg.reply_to_message_id is None
    assert msg.reply_to_is_bot is None
    assert msg.reply_to_username is None


def test_decode_forum_topic_created_from_json() -> None:
    """Test that forum_topic_created is correctly decoded from raw JSON.

    This test uses the exact JSON structure from the Telegram Bot API
    to ensure our msgspec schema correctly parses real payloads.
    """
    payload = """{
        "update_id": 123456789,
        "message": {
            "message_id": 187,
            "from": {
                "id": 12345,
                "is_bot": false,
                "first_name": "Test",
                "username": "testuser"
            },
            "chat": {
                "id": -1001234567890,
                "title": "Test Forum",
                "type": "supergroup",
                "is_forum": true
            },
            "date": 1705395600,
            "message_thread_id": 163,
            "is_topic_message": true,
            "reply_to_message": {
                "message_id": 163,
                "from": {
                    "id": 8312076814,
                    "is_bot": true,
                    "first_name": "Takopi",
                    "username": "TakopiBot"
                },
                "chat": {
                    "id": -1001234567890,
                    "title": "Test Forum",
                    "type": "supergroup",
                    "is_forum": true
                },
                "date": 1705395500,
                "message_thread_id": 163,
                "forum_topic_created": {
                    "name": "party-testing7 @main",
                    "icon_color": 7322096
                }
            },
            "text": "Hello"
        }
    }"""

    update = decode_update(payload)
    assert update.message is not None
    assert update.message.reply_to_message is not None
    assert update.message.reply_to_message.forum_topic_created is not None
    assert update.message.reply_to_message.forum_topic_created.name == "party-testing7 @main"
    assert update.message.reply_to_message.forum_topic_created.icon_color == 7322096
    assert update.message.reply_to_message.from_ is not None
    assert update.message.reply_to_message.from_.is_bot is True

    # Now test the full parsing
    msg = parse_incoming_update(update, chat_id=-1001234567890)
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.reply_to_is_bot is None  # Should be None due to forum_topic_created
