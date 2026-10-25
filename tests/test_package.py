"""Package metadata tests."""

import codeatlas


def test_version_is_exposed() -> None:
    """The top-level package exposes stable metadata."""
    assert codeatlas.__version__ == "0.5.0"
    assert codeatlas.API_VERSION == "0.1.0"
