"""Validated CodeAtlas settings sourced from CODEATLAS_* variables."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported application environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    """Supported structured log renderers."""

    JSON = "json"
    CONSOLE = "console"


class ApplicationSettings(BaseModel):
    """General process settings."""

    environment: Environment = Environment.DEVELOPMENT
    service_name: str = Field(default="codeatlas", min_length=1, max_length=64)
    shutdown_timeout_seconds: float = Field(default=10.0, ge=1.0, le=120.0)


class LoggingSettings(BaseModel):
    """Structured logging settings."""

    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    format: LogFormat = LogFormat.CONSOLE


class DatabaseSettings(BaseModel):
    """SQLite settings reserved for later persistence work."""

    url: str = Field(default="sqlite:///./data/codeatlas.db", min_length=1)
    busy_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60.0)

    @model_validator(mode="after")
    def require_sqlite(self) -> DatabaseSettings:
        """Restrict M1 to the documented SQLite backend."""
        if not self.url.startswith("sqlite:///"):
            raise ValueError("database.url must use sqlite:///")
        return self


class QdrantSettings(BaseModel):
    """Qdrant connection settings; no client is created in M1."""

    enabled: bool = False
    url: HttpUrl = HttpUrl("http://qdrant:6333")
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=10.0, ge=0.1, le=120.0)


class OllamaSettings(BaseModel):
    """Ollama settings; model connectivity is not implemented in M1."""

    enabled: bool = False
    base_url: HttpUrl = HttpUrl("http://localhost:11434")
    model: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)

    @model_validator(mode="after")
    def require_model_when_enabled(self) -> OllamaSettings:
        """Require a model only when the provider is enabled."""
        if self.enabled and self.model is None:
            raise ValueError("ollama.model is required when Ollama is enabled")
        return self


class DiscordSettings(BaseModel):
    """Discord settings; the Discord adapter is not implemented in M1."""

    enabled: bool = False
    token: SecretStr | None = None
    forum_channel_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_credentials_when_enabled(self) -> DiscordSettings:
        """Require Discord credentials only for an enabled adapter."""
        token_missing = self.token is None or not self.token.get_secret_value().strip()
        if self.enabled and (token_missing or self.forum_channel_id is None):
            raise ValueError("Discord token and forum_channel_id are required when enabled")
        return self


class GitHubSettings(BaseModel):
    """GitHub settings; network integration is deferred beyond M1."""

    enabled: bool = False
    token: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    api_url: HttpUrl = HttpUrl("https://api.github.com")
    timeout_seconds: float = Field(default=15.0, ge=0.1, le=120.0)

    @model_validator(mode="after")
    def require_token_when_enabled(self) -> GitHubSettings:
        """Require a token only when GitHub integration is enabled."""
        token_missing = self.token is None or not self.token.get_secret_value().strip()
        if self.enabled and token_missing:
            raise ValueError("github.token is required when GitHub is enabled")
        return self


class SecuritySettings(BaseModel):
    """Security defaults applied by future adapters."""

    plugin_allowlist: tuple[str, ...] = ()
    allow_repository_writes: bool = False
    max_http_response_bytes: int = Field(default=2_000_000, ge=1_024, le=20_000_000)

    @model_validator(mode="after")
    def prohibit_writes(self) -> SecuritySettings:
        """Keep M1 and the planned MVP read-only."""
        if self.allow_repository_writes:
            raise ValueError("repository writes are not supported")
        return self


class IndexingLimits(BaseModel):
    """Resource limits for the future indexer."""

    max_file_bytes: int = Field(default=1_000_000, ge=1_024, le=50_000_000)
    max_repository_files: int = Field(default=100_000, ge=1, le=1_000_000)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    operation_timeout_seconds: float = Field(default=300.0, ge=1.0, le=3_600.0)


class CodeAtlasSettings(BaseSettings):
    """Root settings model for all CodeAtlas components."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CODEATLAS_",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    application: ApplicationSettings = Field(default_factory=ApplicationSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    indexing: IndexingLimits = Field(default_factory=IndexingLimits)


def load_settings(env_file: Path | str | None = None) -> CodeAtlasSettings:
    """Load and validate settings, optionally from an explicit dotenv file."""
    if env_file is None:
        return CodeAtlasSettings()
    return CodeAtlasSettings(_env_file=env_file)
