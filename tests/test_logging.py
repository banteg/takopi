import logging

from takopi.logging import RedactTokenFilter, setup_logging


class TestRedactTokenFilter:
    def test_redacts_bot_token(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="https://api.telegram.org/bot123456789:ABCdefGHI_jkl/sendMessage",
            args=(),
            exc_info=None,
        )

        filt = RedactTokenFilter()
        filt.filter(record)

        assert "123456789" not in record.getMessage()
        assert "bot[REDACTED]" in record.getMessage()

    def test_redacts_bare_token(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Token is 123456789:ABCDEFGHIJ_klmnop",
            args=(),
            exc_info=None,
        )

        filt = RedactTokenFilter()
        filt.filter(record)

        assert "123456789" not in record.getMessage()
        assert "[REDACTED_TOKEN]" in record.getMessage()

    def test_no_token_unchanged(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="This is a normal message",
            args=(),
            exc_info=None,
        )

        filt = RedactTokenFilter()
        result = filt.filter(record)

        assert result is True
        assert record.getMessage() == "This is a normal message"

    def test_handles_format_args(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Token: bot%s:%s",
            args=("123456789", "ABCdefGHI_jkl"),
            exc_info=None,
        )

        filt = RedactTokenFilter()
        filt.filter(record)

        assert "123456789" not in record.getMessage()


class TestSetupLogging:
    def test_setup_debug_mode(self) -> None:
        setup_logging(debug=True)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_setup_info_mode(self) -> None:
        setup_logging(debug=False)
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_silences_noisy_loggers(self) -> None:
        setup_logging()
        assert logging.getLogger("markdown_it").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
