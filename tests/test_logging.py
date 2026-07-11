"""Structured logging tests."""

import io
import json
import logging

import structlog

from codeatlas.config.settings import LogFormat, LoggingSettings
from codeatlas.infrastructure.logging import configure_logging


def test_structlog_redacts_nested_secrets() -> None:
    """Credential-like keys are recursively redacted from JSON events."""
    output = io.StringIO()
    configure_logging(LoggingSettings(format=LogFormat.JSON), stream=output)

    structlog.get_logger("test").info(
        "redaction_check",
        token="top-secret",
        nested={"authorization": "Bearer hidden", "safe": "visible"},
    )

    event = json.loads(output.getvalue())
    assert event["event"] == "redaction_check"
    assert event["logger"] == "test"
    assert event["level"] == "info"
    assert "timestamp" in event
    assert event["token"] == "[REDACTED]"
    assert event["nested"]["authorization"] == "[REDACTED]"
    assert "top-secret" not in output.getvalue()
    assert "Bearer hidden" not in output.getvalue()


def test_standard_logging_is_structured_and_redacted() -> None:
    """Standard library logs share the structured processor chain."""
    output = io.StringIO()
    configure_logging(LoggingSettings(format=LogFormat.JSON), stream=output)
    logging.getLogger("stdlib").warning("safe event", extra={"password": "hidden"})

    rendered = output.getvalue()
    assert "safe event" in rendered
    assert "hidden" not in rendered
