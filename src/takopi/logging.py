from __future__ import annotations

import errno
import io
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TextIO, cast

import structlog
from structlog.types import Processor

TELEGRAM_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
TELEGRAM_BARE_TOKEN_RE = re.compile(r"\b\d+:[A-Za-z0-9_-]{10,}\b")

_LEVELS: dict[str, int] = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "exception": 40,
    "critical": 50,
}

_MIN_LEVEL = _LEVELS["info"]
_PIPELINE_LEVEL_NAME = "debug"

_suppress_below: ContextVar[int | None] = ContextVar(
    "takopi_suppress_below", default=None
)
_log_file_handle: TextIO | None = None


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _level_value(value: str | None, *, default: str = "info") -> int:
    if not value:
        return _LEVELS[default]
    level = _LEVELS.get(value.strip().lower())
    return level if level is not None else _LEVELS[default]


def pipeline_log_level() -> str:
    return _PIPELINE_LEVEL_NAME


def log_pipeline(logger: Any, event: str, **fields: Any) -> None:
    if _PIPELINE_LEVEL_NAME == "info":
        logger.info(event, **fields)
    else:
        logger.debug(event, **fields)


def _drop_below_level(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    level_value = _LEVELS.get(method_name, 0)
    if level_value < _MIN_LEVEL:
        raise structlog.DropEvent
    suppress = _suppress_below.get()
    if suppress is not None and level_value < suppress:
        raise structlog.DropEvent
    return event_dict


def _redact_text(value: str) -> str:
    redacted = TELEGRAM_TOKEN_RE.sub("bot[REDACTED]", value)
    return TELEGRAM_BARE_TOKEN_RE.sub("[REDACTED_TOKEN]", redacted)


def _redact_value(value: Any, memo: dict[int, Any]) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return _redact_text(value.decode("utf-8", errors="replace"))
    obj_id = id(value)
    if obj_id in memo:
        return memo[obj_id]
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        memo[obj_id] = redacted
        for key, val in value.items():
            redacted[key] = _redact_value(val, memo)
        return redacted
    if isinstance(value, list):
        redacted_list: list[Any] = []
        memo[obj_id] = redacted_list
        redacted_list.extend(_redact_value(item, memo) for item in value)
        return redacted_list
    if isinstance(value, tuple):
        redacted_tuple: list[Any] = []
        memo[obj_id] = redacted_tuple
        redacted_tuple.extend(_redact_value(item, memo) for item in value)
        return tuple(redacted_tuple)
    if isinstance(value, set):
        redacted_set: set[Any] = set()
        memo[obj_id] = redacted_set
        redacted_set.update(_redact_value(item, memo) for item in value)
        return redacted_set
    return value


def _redact_event_dict(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    return _redact_value(event_dict, memo={})


def _file_sink(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    if _log_file_handle is None:
        return event_dict
    try:
        payload = structlog.processors.JSONRenderer(default=str)(
            logger, method_name, dict(event_dict)
        )
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        _log_file_handle.write(payload + "\n")
        _log_file_handle.flush()
    except Exception:  # noqa: BLE001
        return event_dict
    return event_dict


def _add_logger_name(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    if "logger" in event_dict:
        return event_dict
    name = event_dict.pop("logger_name", None)
    if isinstance(name, str) and name:
        event_dict["logger"] = name
        return event_dict
    fallback = getattr(logger, "name", None)
    if isinstance(fallback, str) and fallback:
        event_dict["logger"] = fallback
    return event_dict


def get_logger(name: str | None = None) -> Any:
    if name:
        return structlog.get_logger(logger_name=name)
    return structlog.get_logger()


def bind_run_context(**fields: Any) -> None:
    structlog.contextvars.bind_contextvars(**fields)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


class SafeWriter(io.TextIOBase):
    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._closed = False

    def write(self, message: str) -> int:
        if self._closed:
            return 0
        try:
            return self._stream.write(message)
        except (BrokenPipeError, ValueError):
            self._close()
            return 0
        except OSError as exc:
            if exc.errno == errno.EPIPE:
                self._close()
                return 0
            raise

    def flush(self) -> None:
        if self._closed:
            return
        try:
            self._stream.flush()
        except (BrokenPipeError, ValueError):
            self._close()
        except OSError as exc:
            if exc.errno == errno.EPIPE:
                self._close()
                return
            raise

    def isatty(self) -> bool:
        isatty = getattr(self._stream, "isatty", None)
        return bool(isatty()) if callable(isatty) else False

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.close()
        except Exception:  # noqa: BLE001
            return


def setup_logging(
    *, debug: bool = False, cache_logger_on_first_use: bool = False
) -> None:
    global _MIN_LEVEL, _PIPELINE_LEVEL_NAME
    global _log_file_handle

    level_name = os.environ.get("TAKOPI_LOG_LEVEL")
    if debug:
        level_name = "debug"
    _MIN_LEVEL = _level_value(level_name, default="info")

    trace_pipeline = _truthy(os.environ.get("TAKOPI_TRACE_PIPELINE"))
    _PIPELINE_LEVEL_NAME = "info" if trace_pipeline else "debug"

    format_value = os.environ.get("TAKOPI_LOG_FORMAT", "console").strip().lower()
    color_override = os.environ.get("TAKOPI_LOG_COLOR")
    is_tty = sys.stdout.isatty() if color_override is None else _truthy(color_override)
    if format_value == "json":
        renderer: Any = structlog.processors.JSONRenderer(default=str)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=is_tty)

    safe_stream = cast(TextIO, SafeWriter(sys.stdout))
    log_file = os.environ.get("TAKOPI_LOG_FILE")
    if _log_file_handle is not None:
        try:
            _log_file_handle.close()
        except Exception:  # noqa: BLE001
            _log_file_handle = None
        else:
            _log_file_handle = None
    if log_file:
        try:
            _log_file_handle = open(  # noqa: SIM115
                log_file, "a", encoding="utf-8"
            )
        except OSError:
            _log_file_handle = None

    processors = cast(
        list[Processor],
        [
            _drop_below_level,
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            _add_logger_name,
        ],
    )
    if format_value == "json":
        processors.append(structlog.processors.format_exc_info)
    processors.extend(
        cast(
            list[Processor],
            [
                _redact_event_dict,
                _file_sink,
                cast(Processor, renderer),
            ],
        )
    )

    structlog.configure(
        processors=processors,
        logger_factory=structlog.PrintLoggerFactory(file=safe_stream),
        cache_logger_on_first_use=cache_logger_on_first_use,
    )


@contextmanager
def suppress_logs(level: str = "warning"):
    token = _suppress_below.set(_level_value(level, default="warning"))
    try:
        yield
    finally:
        _suppress_below.reset(token)
