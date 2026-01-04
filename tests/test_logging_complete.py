import logging
from typing import Any, cast

from takopi.logging import RedactTokenFilter, setup_logging


def test_redact_token_filter_redacts_bot_token() -> None:
    redactor = RedactTokenFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Token: bot123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        args=(),
        exc_info=None,
    )

    result = redactor.filter(record)
    assert result is True
    # The token gets redacted, either as bot[REDACTED] or [REDACTED_TOKEN]
    assert "bot[REDACTED]" in record.msg or "[REDACTED_TOKEN]" in record.msg
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz" not in record.msg


def test_redact_token_filter_redacts_bare_token() -> None:
    redactor = RedactTokenFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Bare token: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        args=(),
        exc_info=None,
    )

    result = redactor.filter(record)
    assert result is True
    assert "[REDACTED_TOKEN]" in record.msg
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz" not in record.msg


def test_redact_token_filter_handles_type_error() -> None:
    redactor = RedactTokenFilter()

    class BadRecord:
        def getMessage(self):
            raise TypeError("bad")

    result = redactor.filter(cast(Any, BadRecord()))
    assert result is True


def test_redact_token_filter_handles_value_error() -> None:
    redactor = RedactTokenFilter()

    class BadRecord:
        def getMessage(self):
            raise ValueError("bad")

    result = redactor.filter(cast(Any, BadRecord()))
    assert result is True


def test_redact_token_filter_preserves_message_without_token() -> None:
    redactor = RedactTokenFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="No token here",
        args=(),
        exc_info=None,
    )

    result = redactor.filter(record)
    assert result is True
    assert record.msg == "No token here"


def test_redact_token_filter_clears_args_when_redacted() -> None:
    redactor = RedactTokenFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Token: bot%s",
        args=("123456789:ABCdefGHIjklMNOpqrsTUVwxyz",),
        exc_info=None,
    )

    result = redactor.filter(record)
    assert result is True
    # The token gets redacted
    assert "bot[REDACTED]" in record.msg or "[REDACTED_TOKEN]" in record.msg
    assert record.args == ()


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
