"""Safe exception behavior tests."""

from codeatlas.domain.errors import ProviderError


def test_internal_cause_is_not_exposed_in_public_text() -> None:
    """UI-safe text and sensitive internal diagnostics remain separate."""
    cause = RuntimeError("token=private-value")
    error = ProviderError(internal_cause=cause)

    assert "private-value" not in str(error)
    assert "private-value" not in repr(error)
    assert error.internal_cause is cause
