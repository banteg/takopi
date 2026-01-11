from takopi.telegram import (
    TelegramCallbackQuery,
    TelegramIncomingMessage,
    parse_incoming_update,
)


def test_parse_incoming_update_maps_fields() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": "hello",
            "chat": {"id": 123, "type": "supergroup", "is_forum": True},
            "from": {"id": 99},
            "reply_to_message": {"message_id": 5, "text": "prev"},
        },
    }

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.transport == "telegram"
    assert msg.chat_id == 123
    assert msg.message_id == 10
    assert msg.text == "hello"
    assert msg.reply_to_message_id == 5
    assert msg.reply_to_text == "prev"
    assert msg.sender_id == 99
    assert msg.thread_id is None
    assert msg.is_topic_message is None
    assert msg.chat_type == "supergroup"
    assert msg.is_forum is True
    assert msg.voice is None
    assert msg.document is None
    assert msg.raw == update["message"]


def test_parse_incoming_update_filters_non_matching_chat() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": "hello",
            "chat": {"id": 123},
        },
    }

    assert parse_incoming_update(update, chat_id=999) is None


def test_parse_incoming_update_filters_non_text_and_non_voice() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": 123},
            "location": {"latitude": 1.0, "longitude": 2.0},
        },
    }

    assert parse_incoming_update(update, chat_id=123) is None


def test_parse_incoming_update_voice_message() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": 123},
            "voice": {
                "file_id": "voice-id",
                "file_unique_id": "uniq",
                "duration": 3,
                "mime_type": "audio/ogg",
                "file_size": 1234,
            },
        },
    }

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
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "caption": "/file put incoming/doc.txt",
            "chat": {"id": 123},
            "document": {
                "file_id": "doc-id",
                "file_unique_id": "uniq",
                "file_name": "doc.txt",
                "mime_type": "text/plain",
                "file_size": 4321,
            },
        },
    }

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
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "caption": "/file put incoming/photo.jpg",
            "chat": {"id": 123},
            "photo": [
                {
                    "file_id": "small",
                    "file_unique_id": "uniq-small",
                    "file_size": 100,
                    "width": 90,
                    "height": 90,
                },
                {
                    "file_id": "large",
                    "file_unique_id": "uniq-large",
                    "file_size": 1000,
                    "width": 800,
                    "height": 600,
                },
            ],
        },
    }

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.text == "/file put incoming/photo.jpg"
    assert msg.document is not None
    assert msg.document.file_id == "large"
    assert msg.document.file_name is None
    assert msg.document.file_size == 1000


def test_parse_incoming_update_media_group_id() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": 123},
            "media_group_id": "group-1",
            "photo": [
                {
                    "file_id": "large",
                    "file_unique_id": "uniq-large",
                    "file_size": 1000,
                    "width": 800,
                    "height": 600,
                }
            ],
        },
    }

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.media_group_id == "group-1"


def test_parse_incoming_update_video_message() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "caption": "/file put incoming/video.mp4",
            "chat": {"id": 123},
            "video": {
                "file_id": "video-id",
                "file_unique_id": "uniq",
                "file_name": "video.mp4",
                "mime_type": "video/mp4",
                "file_size": 4242,
            },
        },
    }

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
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "caption": "/file put incoming/sticker.webp",
            "chat": {"id": 123},
            "sticker": {
                "file_id": "sticker-id",
                "file_unique_id": "uniq",
                "file_size": 2468,
            },
        },
    }

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
    update = {
        "update_id": 1,
        "callback_query": {
            "id": "cbq-1",
            "data": "takopi:cancel",
            "from": {"id": 321},
            "message": {
                "message_id": 55,
                "chat": {"id": 123},
            },
        },
    }

    msg = parse_incoming_update(update, chat_id=123)
    assert isinstance(msg, TelegramCallbackQuery)
    assert msg.transport == "telegram"
    assert msg.chat_id == 123
    assert msg.message_id == 55
    assert msg.callback_query_id == "cbq-1"
    assert msg.data == "takopi:cancel"
    assert msg.sender_id == 321


def test_parse_incoming_update_topic_fields() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": "hello",
            "message_thread_id": 77,
            "is_topic_message": True,
            "chat": {"id": -100, "type": "supergroup", "is_forum": True},
        },
    }

    msg = parse_incoming_update(update, chat_id=-100)
    assert isinstance(msg, TelegramIncomingMessage)
    assert msg.thread_id == 77
    assert msg.is_topic_message is True
    assert msg.chat_type == "supergroup"
    assert msg.is_forum is True
