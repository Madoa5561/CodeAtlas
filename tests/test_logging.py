"""Structured logging tests."""

import io
import json
import logging

import structlog
from pydantic import SecretStr

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


def test_redaction_handles_recursive_collections_and_secret_str() -> None:
    """Lists, tuples, nested mappings, and SecretStr values are recursively safe."""
    output = io.StringIO()
    configure_logging(LoggingSettings(format=LogFormat.JSON), stream=output)

    structlog.get_logger("test").info(
        "normal status message",
        nested={
            "list": [{"password": "list-secret"}],
            "tuple": ({"api_key": "tuple-secret"},),
            "dict_with_list": {"items": [{"authorization": "Bearer nested-secret"}]},
            "deep": {"one": {"two": {"secret": "deep-secret"}}},
        },
        credential_text="token=string-secret",
        secret_object=SecretStr("object-secret"),
        ordinary="tokenization is a normal word",
    )

    event = json.loads(output.getvalue())
    assert event["nested"]["list"][0]["password"] == "[REDACTED]"
    assert event["nested"]["tuple"][0]["api_key"] == "[REDACTED]"
    assert event["nested"]["dict_with_list"]["items"][0]["authorization"] == "[REDACTED]"
    assert event["nested"]["deep"]["one"]["two"]["secret"] == "[REDACTED]"
    assert event["credential_text"] == "token=[REDACTED]"
    assert event["secret_object"] == "[REDACTED]"
    assert event["ordinary"] == "tokenization is a normal word"
    assert event["event"] == "normal status message"


def test_standard_logging_is_structured_and_redacted() -> None:
    """Standard library logs share the structured processor chain."""
    output = io.StringIO()
    configure_logging(LoggingSettings(format=LogFormat.JSON), stream=output)
    logging.getLogger("stdlib").warning("safe event", extra={"password": "hidden"})

    rendered = output.getvalue()
    assert "safe event" in rendered
    assert "hidden" not in rendered


def test_standard_logging_exception_does_not_expose_secret() -> None:
    """Standard logging retains an exception type without its message or stack trace."""
    output = io.StringIO()
    configure_logging(LoggingSettings(format=LogFormat.JSON), stream=output)

    try:
        raise RuntimeError("token=exception-secret")
    except RuntimeError:
        logging.getLogger("stdlib").exception("operation failed")

    events = output.getvalue().splitlines()
    assert len(events) == 1
    event = json.loads(events[0])
    assert event["exception_type"] == "RuntimeError"
    assert "exception-secret" not in events[0]
