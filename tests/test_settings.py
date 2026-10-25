"""Settings validation and secret handling tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from codeatlas.config.settings import (
    CodeAtlasSettings,
    DeepSeekSettings,
    DiscordSettings,
    IndexingLimits,
)


def test_disabled_integrations_do_not_require_secrets() -> None:
    """Optional integrations can remain disabled without credentials."""
    settings = CodeAtlasSettings(_env_file=None)
    assert settings.discord.enabled is False
    assert settings.discord.token is None
    assert settings.deepseek.enabled is False
    assert settings.deepseek.api_key is None


def test_enabled_deepseek_requires_api_key() -> None:
    """DeepSeek fails fast when enabled without a key."""
    with pytest.raises(ValidationError):
        DeepSeekSettings(enabled=True)
    with pytest.raises(ValidationError):
        DeepSeekSettings(enabled=True, api_key="   ")
    assert DeepSeekSettings(enabled=True, api_key="sk-test").model == "deepseek-flash"


def test_only_one_model_provider_can_be_enabled() -> None:
    """Enabling Ollama and DeepSeek together is rejected deterministically."""
    with pytest.raises(ValidationError):
        CodeAtlasSettings(
            _env_file=None,
            ollama={"enabled": True, "model": "model"},
            deepseek={"enabled": True, "api_key": "sk-test"},
        )


def test_deepseek_secret_is_hidden_from_repr() -> None:
    """The API key is a SecretStr and never appears in representations."""
    settings = DeepSeekSettings(enabled=True, api_key="sk-hidden")
    assert "sk-hidden" not in repr(settings)
    assert "**********" in repr(settings)


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
    assert settings.deepseek.enabled is False
