import io
import sys

import pytest

from takopi.logging import (
    _redact_text,
    bind_run_context,
    clear_context,
    get_logger,
    setup_logging,
    suppress_logs,
)


class TestRedactText:
    def test_redacts_bot_token_url(self) -> None:
        text = "https://api.telegram.org/bot123456789:ABCdefGHI_jkl/sendMessage"
        result = _redact_text(text)
        assert "123456789" not in result
        assert "bot[REDACTED]" in result

    def test_redacts_bare_token(self) -> None:
        text = "Token is 123456789:ABCDEFGHIJ_klmnop"
        result = _redact_text(text)
        assert "123456789" not in result
        assert "[REDACTED_TOKEN]" in result

    def test_no_token_unchanged(self) -> None:
        text = "This is a normal message"
        result = _redact_text(text)
        assert result == text


class TestSetupLogging:
    def test_setup_returns_without_error(self) -> None:
        setup_logging(debug=True)
        setup_logging(debug=False)


class TestGetLogger:
    def test_returns_logger(self) -> None:
        logger = get_logger("test")
        assert logger is not None

    def test_logger_without_name(self) -> None:
        logger = get_logger()
        assert logger is not None


class TestContextManagement:
    def test_bind_and_clear(self) -> None:
        bind_run_context(workspace="test", engine="codex")
        clear_context()


class TestSuppressLogs:
    def test_suppress_logs_context(self) -> None:
        with suppress_logs("error"):
            logger = get_logger("test")
            logger.info("this should be suppressed")
