"""Structured logging configuration with recursive secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from contextvars import Token
from typing import TextIO

import structlog
from pydantic import SecretStr
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.typing import EventDict, Processor

from codeatlas.config.settings import LogFormat, LoggingSettings

_REDACTED = "[REDACTED]"
_SENSITIVE_FRAGMENTS = ("authorization", "password", "secret", "token", "api_key", "apikey")
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(authorization|password|secret|token|api[_-]?key)\s*([:=])\s*([^\s,;]+)"
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)


def _redact_value(value: object) -> object:
    if isinstance(value, SecretStr):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED if _is_sensitive_key(key) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        return _SENSITIVE_TEXT.sub(r"\1\2[REDACTED]", value)
    return value


def sanitize_exception_info(
    _logger: object,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Remove stack traces and exception messages while retaining the exception type."""
    record = event_dict.get("_record")
    exception_info = event_dict.pop("exc_info", None)
    exception_type: type[BaseException] | None = None
    if isinstance(record, logging.LogRecord) and record.exc_info is not None:
        exception_type = record.exc_info[0]
        record.exc_info = None
        record.exc_text = None
    elif isinstance(exception_info, tuple) and exception_info:
        candidate = exception_info[0]
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            exception_type = candidate
    if exception_type is not None:
        event_dict["exception_type"] = exception_type.__name__
    return event_dict


def redact_sensitive_data(
    _logger: object,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Redact values whose keys indicate credentials, including nested mappings."""
    return {
        key: _redact_value(value) if not _is_sensitive_key(key) else _REDACTED
        for key, value in event_dict.items()
    }


def configure_logging(settings: LoggingSettings, *, stream: TextIO | None = None) -> None:
    """Configure structlog and the standard logging module."""
    output = stream or sys.stdout
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        sanitize_exception_info,
        redact_sensitive_data,
    ]
    renderer: Processor
    if settings.format is LogFormat.JSON:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(output)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.level)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def bind_log_context(**values: str) -> Mapping[str, Token[str | None]]:
    """Bind request or correlation identifiers to the current context."""
    return bind_contextvars(**values)


def clear_log_context() -> None:
    """Clear all logging context variables for the current context."""
    clear_contextvars()
