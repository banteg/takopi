from __future__ import annotations

import errno
import logging
import re
import sys

import structlog


TELEGRAM_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
TELEGRAM_BARE_TOKEN_RE = re.compile(r"\b\d+:[A-Za-z0-9_-]{10,}\b")


def redact_token_processor(_, __, event_dict):
    """Processor to redact Telegram tokens from log messages."""
    message = str(event_dict.get("event", ""))

    redacted = TELEGRAM_TOKEN_RE.sub("bot[REDACTED]", message)
    redacted = TELEGRAM_BARE_TOKEN_RE.sub("[REDACTED_TOKEN]", redacted)

    if redacted != message:
        event_dict["event"] = redacted

    return event_dict


class SafeStreamHandler(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, BrokenPipeError):
            try:
                self.stream.close()
            except Exception:
                pass
            return
        if isinstance(exc, OSError) and exc.errno == errno.EPIPE:
            try:
                self.stream.close()
            except Exception:
                pass
            return
        super().handleError(record)


def setup_logging(*, debug: bool = False) -> None:
    """Configure structlog with console output and token redaction."""

    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_token_processor,
            structlog.dev.ConsoleRenderer(colors=True)
            if debug
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    import logging

    stdlib_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=stdlib_level,
        force=True,
    )

    logging.getLogger("markdown_it").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
