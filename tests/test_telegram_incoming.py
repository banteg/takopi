from takopi.telegram import parse_incoming_update


def test_parse_incoming_update_maps_fields() -> None:
    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": "hello",
            "chat": {"id": 123},
            "from": {"id": 99},
            "reply_to_message": {"message_id": 5, "text": "prev"},
        },
    }

    msg = parse_incoming_update(update, chat_id=123)
    assert msg is not None
    assert msg.transport == "telegram"
    assert msg.chat_id == 123
    assert msg.message_id == 10
    assert msg.text == "hello"
    assert msg.reply_to_message_id == 5
    assert msg.reply_to_text == "prev"
    assert msg.sender_id == 99
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


def test_parse_incoming_update_filters_non_text() -> None:
    update = {
        "update_id": 1,
        "message": {"message_id": 10, "chat": {"id": 123}},
    }

    assert parse_incoming_update(update, chat_id=123) is None
