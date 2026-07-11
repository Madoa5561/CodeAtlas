"""Composition root tests."""

import pytest

from codeatlas.bootstrap import create_application
from codeatlas.config.settings import CodeAtlasSettings
from codeatlas.domain.errors import ConfigurationError


@pytest.mark.asyncio
async def test_bootstrap_application_lifecycle() -> None:
    """The M1 container starts and stops without external connections."""
    application = create_application(settings=CodeAtlasSettings(_env_file=None))
    assert application.started is False
    async with application:
        assert application.started is True
    assert application.started is False


def test_bootstrap_wraps_configuration_details(tmp_path: pytest.TempPathFactory) -> None:
    """Invalid dotenv values become a safe configuration error."""
    env_file = tmp_path / ".env"
    env_file.write_text("CODEATLAS_INDEXING__MAX_CONCURRENCY=0\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as captured:
        create_application(env_file=env_file)
    assert "MAX_CONCURRENCY" not in str(captured.value)
