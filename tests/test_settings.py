"""Settings validation and secret handling tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from codeatlas.config.settings import CodeAtlasSettings, DiscordSettings, IndexingLimits


def test_disabled_integrations_do_not_require_secrets() -> None:
    """Optional integrations can remain disabled without credentials."""
    settings = CodeAtlasSettings(_env_file=None)
    assert settings.discord.enabled is False
    assert settings.discord.token is None


def test_enabled_discord_requires_credentials() -> None:
    """An enabled integration fails fast when required settings are absent."""
    with pytest.raises(ValidationError):
        DiscordSettings(enabled=True)


def test_indexing_limits_are_bounded() -> None:
    """Unsafe resource limits are rejected."""
    with pytest.raises(ValidationError):
        IndexingLimits(max_concurrency=0)


def test_secret_is_hidden_from_repr() -> None:
    """Pydantic SecretStr values never appear in model representations."""
    settings = DiscordSettings(token="discord-secret")
    assert "discord-secret" not in repr(settings)
    assert "**********" in repr(settings)


def test_environment_prefix_and_nested_delimiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """CODEATLAS_ nested environment variables override defaults."""
    monkeypatch.setenv("CODEATLAS_INDEXING__MAX_CONCURRENCY", "7")
    settings = CodeAtlasSettings(_env_file=None)
    assert settings.indexing.max_concurrency == 7


def test_plugin_allowlist_preserves_unset_empty_and_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings preserve all three allowlist policy states."""
    assert CodeAtlasSettings(_env_file=None).security.plugin_allowlist is None

    monkeypatch.setenv("CODEATLAS_SECURITY__PLUGIN_ALLOWLIST", "[]")
    assert CodeAtlasSettings(_env_file=None).security.plugin_allowlist == ()

    monkeypatch.setenv(
        "CODEATLAS_SECURITY__PLUGIN_ALLOWLIST",
        '["example.allowed"]',
    )
    assert CodeAtlasSettings(_env_file=None).security.plugin_allowlist == ("example.allowed",)


def test_env_example_covers_root_setting_sections() -> None:
    """The example dotenv file documents every section and can be loaded as-is."""
    example = Path(".env.example").read_text(encoding="utf-8")
    for section in CodeAtlasSettings.model_fields:
        assert f"CODEATLAS_{section.upper()}__" in example
    settings = CodeAtlasSettings(_env_file=".env.example")
    assert settings.discord.enabled is False
    assert settings.ollama.enabled is False
