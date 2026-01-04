import logging

from takopi.logging import _redact_text, setup_logging


def test_redact_text_redacts_bot_token() -> None:
    result = _redact_text("Token: bot123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
    assert "bot[REDACTED]" in result
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz" not in result


def test_redact_text_redacts_bare_token() -> None:
    result = _redact_text("Bare token: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz")
    assert "[REDACTED_TOKEN]" in result
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz" not in result


def test_redact_text_preserves_message_without_token() -> None:
    result = _redact_text("No token here")
    assert result == "No token here"


def test_setup_logging_debug_mode() -> None:
    root_logger = logging.getLogger()

    # Save original handlers
    original_handlers = root_logger.handlers[:]

    setup_logging(debug=True)

    # Clean up
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    # Restore original handlers
    for handler in original_handlers:
        root_logger.addHandler(handler)


def test_setup_logging_info_mode() -> None:
    root_logger = logging.getLogger()

    # Save original handlers
    original_handlers = root_logger.handlers[:]

    setup_logging(debug=False)

    # Clean up
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    # Restore original handlers
    for handler in original_handlers:
        root_logger.addHandler(handler)
