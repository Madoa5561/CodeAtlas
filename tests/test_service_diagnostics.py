"""Safe startup diagnostics for the long-running service entrypoint."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeatlas.config.settings import CodeAtlasSettings
from codeatlas.domain.errors import CodeAtlasError
from codeatlas.entrypoints.service import _safe_error_details


def test_validation_failure_lists_fields_without_secret_values() -> None:
    """A config typo logs field locations only, never the supplied secret."""
    with pytest.raises(ValidationError) as captured:
        CodeAtlasSettings(
            _env_file=None,
            deepseek={
                "enabled": True,
                "api_key": "sk-super-secret-value",
                "max_retries": "5s",
            },
        )

    details = _safe_error_details(captured.value)

    assert "deepseek.max_retries" in details
    assert "sk-super-secret-value" not in details


def test_codeatlas_error_uses_safe_error_code() -> None:
    """Application errors are represented by their stable safe code."""
    assert _safe_error_details(CodeAtlasError()) == f"error_code={CodeAtlasError.code}"


def test_unexpected_error_uses_only_the_type_name() -> None:
    """Unexpected exceptions never contribute their message to logs."""
    details = _safe_error_details(RuntimeError("internal detail"))

    assert details == "error_type=RuntimeError"
    assert "internal detail" not in details
