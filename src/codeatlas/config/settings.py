"""Validated CodeAtlas settings sourced from CODEATLAS_* variables."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

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
    """SQLite transactional index settings."""

    url: str = Field(default="sqlite:///./data/codeatlas.db", min_length=1)
    busy_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    lease_timeout_seconds: float = Field(default=120.0, ge=5.0, le=3_600.0)
    lock_wait_timeout_seconds: float = Field(default=5.0, ge=0.1, le=120.0)

    @model_validator(mode="after")
    def require_sqlite(self) -> DatabaseSettings:
        """Restrict M1 to the documented SQLite backend."""
        if not self.url.startswith("sqlite:///"):
            raise ValueError("database.url must use sqlite:///")
        return self


class ReplyPolicy(StrEnum):
    """Which ordinary Discord messages receive an automatic answer."""

    ALL = "all"
    MENTION = "mention"
    REPLY = "reply"
    MENTION_OR_REPLY = "mention_or_reply"


class AgentSettings(BaseModel):
    """Budgets and presentation policy for the five-stage agent pipeline."""

    max_iterations: int = Field(default=3, ge=1, le=10)
    max_tool_calls: int = Field(default=12, ge=1, le=100)
    deadline_seconds: float = Field(default=90.0, ge=5.0, le=600.0)
    max_evidence: int = Field(default=12, ge=1, le=50)
    max_plan_steps: int = Field(default=6, ge=1, le=20)
    max_response_chars: int = Field(default=6_000, ge=500, le=20_000)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1_200, ge=1, le=32_000)
    model_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    use_model_planner: bool = False
    fallback_without_model: bool = True


class ConversationSettings(BaseModel):
    """Conversation memory, idempotency, and retention policy."""

    history_limit: int = Field(default=12, ge=1, le=100)
    retention_days: int = Field(default=30, ge=1, le=3650)
    thread_lock_wait_seconds: float = Field(default=20.0, ge=0.1, le=300.0)
    thread_lock_lease_seconds: float = Field(default=300.0, ge=30.0, le=3_600.0)


class QdrantSettings(BaseModel):
    """Qdrant vector projection settings."""

    enabled: bool = False
    url: HttpUrl = HttpUrl("http://qdrant:6333")
    api_key: SecretStr | None = None
    collection: str = Field(
        default="codeatlas_chunks",
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
    )
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)


class OllamaSettings(BaseModel):
    """Ollama chat provider settings."""

    enabled: bool = False
    base_url: HttpUrl = HttpUrl("http://localhost:11434")
    model: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    keep_alive: str = Field(default="5m", max_length=32)
    think: bool = False
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    num_ctx: int = Field(default=8_192, ge=2_048, le=131_072)

    @model_validator(mode="after")
    def require_model_when_enabled(self) -> OllamaSettings:
        """Require a model only when the provider is enabled."""
        if self.enabled and self.model is None:
            raise ValueError("ollama.model is required when Ollama is enabled")
        return self


class DeepSeekSettings(BaseModel):
    """DeepSeek OpenAI-compatible chat provider settings."""

    enabled: bool = False
    api_key: SecretStr | None = None
    base_url: HttpUrl = HttpUrl("https://api.deepseek.com")
    model: str = Field(default="deepseek-flash", min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=10.0)
    thinking: bool = False

    @model_validator(mode="after")
    def require_api_key_when_enabled(self) -> DeepSeekSettings:
        """Require a non-blank API key only for an enabled provider."""
        key_missing = self.api_key is None or not self.api_key.get_secret_value().strip()
        if self.enabled and key_missing:
            raise ValueError("deepseek.api_key is required when DeepSeek is enabled")
        return self


class OpenAISettings(BaseModel):
    """OpenAI chat provider settings."""

    enabled: bool = False
    api_key: SecretStr | None = None
    base_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    model: str = Field(default="gpt-4.1-mini", min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.5, ge=0.0, le=10.0)

    @model_validator(mode="after")
    def require_api_key_when_enabled(self) -> OpenAISettings:
        """Require a non-blank API key only for an enabled provider."""
        key_missing = self.api_key is None or not self.api_key.get_secret_value().strip()
        if self.enabled and key_missing:
            raise ValueError("openai.api_key is required when OpenAI is enabled")
        return self


class AnthropicSettings(BaseModel):
    """Anthropic Messages API provider settings."""

    enabled: bool = False
    api_key: SecretStr | None = None
    base_url: HttpUrl = HttpUrl("https://api.anthropic.com")
    model: str = Field(default="claude-sonnet-4-5", min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)

    @model_validator(mode="after")
    def require_api_key_when_enabled(self) -> AnthropicSettings:
        """Require a non-blank API key only for an enabled provider."""
        key_missing = self.api_key is None or not self.api_key.get_secret_value().strip()
        if self.enabled and key_missing:
            raise ValueError("anthropic.api_key is required when Anthropic is enabled")
        return self


class GeminiSettings(BaseModel):
    """Google Gemini generateContent provider settings."""

    enabled: bool = False
    api_key: SecretStr | None = None
    base_url: HttpUrl = HttpUrl("https://generativelanguage.googleapis.com")
    model: str = Field(default="gemini-2.5-flash", min_length=1, max_length=200)
    timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=5)

    @model_validator(mode="after")
    def require_api_key_when_enabled(self) -> GeminiSettings:
        """Require a non-blank API key only for an enabled provider."""
        key_missing = self.api_key is None or not self.api_key.get_secret_value().strip()
        if self.enabled and key_missing:
            raise ValueError("gemini.api_key is required when Gemini is enabled")
        return self


class DiscordSettings(BaseModel):
    """Discord Forum adapter and response presentation settings."""

    enabled: bool = False
    token: SecretStr | None = None
    forum_channel_id: int | None = Field(default=None, ge=1)
    reply_policy: ReplyPolicy = ReplyPolicy.MENTION_OR_REPLY
    history_limit: int = Field(default=12, ge=1, le=100)
    typing_interval_seconds: float = Field(default=8.0, ge=1.0, le=60.0)
    max_response_chars: int = Field(default=1_900, ge=200, le=2_000)
    locale: str = Field(default="ja", max_length=16)
    ignore_bots: bool = True
    answer_new_forum_posts: bool = True

    @model_validator(mode="after")
    def require_credentials_when_enabled(self) -> DiscordSettings:
        """Require Discord credentials only for an enabled adapter."""
        token_missing = self.token is None or not self.token.get_secret_value().strip()
        if self.enabled and (token_missing or self.forum_channel_id is None):
            raise ValueError("Discord token and forum_channel_id are required when enabled")
        return self


class GitHubSettings(BaseModel):
    """Read-only GitHub API and webhook settings."""

    enabled: bool = False
    token: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    api_url: HttpUrl = HttpUrl("https://api.github.com")
    timeout_seconds: float = Field(default=15.0, ge=0.1, le=120.0)
    default_repository: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$",
        description="Default owner/name used when a GitHub tool omits the repository.",
    )
    webhook_repository: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$",
        description="Owner/name whose push webhooks trigger an incremental index.",
    )
    webhook_repository_id: str | None = Field(
        default=None,
        pattern=r"^repo_[0-9a-f]{32}$",
        description="Registered local repository indexed for matching push webhooks.",
    )

    @model_validator(mode="after")
    def require_token_when_enabled(self) -> GitHubSettings:
        """Require a token only when GitHub integration is enabled."""
        token_missing = self.token is None or not self.token.get_secret_value().strip()
        if self.enabled and token_missing:
            raise ValueError("github.token is required when GitHub is enabled")
        if (self.webhook_repository is None) != (self.webhook_repository_id is None):
            raise ValueError(
                "github.webhook_repository and github.webhook_repository_id must be set together"
            )
        return self


class ApiSettings(BaseModel):
    """Local FastAPI interaction and webhook server settings."""

    enabled: bool = False
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=8080, ge=1, le=65535)
    max_body_bytes: int = Field(default=1_000_000, ge=1_024, le=20_000_000)
    allow_ask: bool = True


class EmbeddingSettings(BaseModel):
    """Embedding provider settings for vector retrieval."""

    enabled: bool = False
    provider: Literal["openai-compatible", "local"] = "openai-compatible"
    api_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    api_key: SecretStr | None = None
    model: str = Field(default="text-embedding-3-small", min_length=1, max_length=128)
    dimensions: int = Field(default=1_536, ge=8, le=4_096)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    batch_size: int = Field(default=16, ge=1, le=128)

    @model_validator(mode="after")
    def require_api_key_for_remote(self) -> EmbeddingSettings:
        """A remote embedding provider requires an API key."""
        if self.enabled and self.provider == "openai-compatible":
            if self.api_key is None or not self.api_key.get_secret_value().strip():
                raise ValueError("embedding.api_key is required for openai-compatible")
        return self


class SecuritySettings(BaseModel):
    """Security defaults applied by future adapters."""

    plugin_allowlist: tuple[str, ...] | None = None
    allow_repository_writes: bool = False
    max_http_response_bytes: int = Field(default=2_000_000, ge=1_024, le=20_000_000)

    @model_validator(mode="after")
    def prohibit_writes(self) -> SecuritySettings:
        """Keep M1 and the planned MVP read-only."""
        if self.allow_repository_writes:
            raise ValueError("repository writes are not supported")
        return self


class IndexingLimits(BaseModel):
    """Resource and failure limits for transactional indexing."""

    max_file_bytes: int = Field(default=1_000_000, ge=1_024, le=50_000_000)
    max_repository_files: int = Field(default=100_000, ge=1, le=1_000_000)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    operation_timeout_seconds: float = Field(default=300.0, ge=1.0, le=3_600.0)
    max_parser_nodes: int = Field(default=200_000, ge=1_000, le=2_000_000)
    max_parser_depth: int = Field(default=512, ge=16, le=2_048)
    max_failure_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    exclude_generated: bool = True
    exclude_minified: bool = True


class RepositorySettings(BaseModel):
    """Safety limits and startup registrations for read-only local repositories."""

    registered_paths: tuple[Path, ...] = ()
    git_executable: str = Field(default="git", pattern=r"^git(?:\.exe)?$")
    ripgrep_executable: str = Field(default="rg", pattern=r"^rg(?:\.exe)?$")
    max_repository_bytes: int = Field(default=2 * 1024**3, ge=1024**2, le=20 * 1024**3)
    max_text_file_bytes: int = Field(default=1024**2, ge=1024, le=10 * 1024**2)
    max_search_file_bytes: int = Field(default=1024**2, ge=1024, le=10 * 1024**2)
    max_directory_entries: int = Field(default=1_000, ge=1, le=10_000)
    max_search_results: int = Field(default=100, ge=1, le=1_000)
    max_diff_bytes: int = Field(default=2 * 1024**2, ge=1024, le=20 * 1024**2)
    max_command_output_bytes: int = Field(default=4 * 1024**2, ge=1024, le=32 * 1024**2)
    command_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    detect_renames: bool = True


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
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    indexing: IndexingLimits = Field(default_factory=IndexingLimits)
    repository: RepositorySettings = Field(default_factory=RepositorySettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    conversation: ConversationSettings = Field(default_factory=ConversationSettings)

    @model_validator(mode="after")
    def require_single_model_provider(self) -> CodeAtlasSettings:
        """Keep model provider selection deterministic by enabling at most one."""
        enabled = [
            self.ollama.enabled,
            self.deepseek.enabled,
            self.openai.enabled,
            self.anthropic.enabled,
            self.gemini.enabled,
        ]
        if sum(enabled) > 1:
            raise ValueError("enable only one model provider")
        if self.qdrant.enabled and not self.embedding.enabled:
            raise ValueError("embedding.enabled is required when qdrant is enabled")
        return self


def load_settings(env_file: Path | str | None = None) -> CodeAtlasSettings:
    """Load and validate settings, optionally from an explicit dotenv file."""
    if env_file is None:
        return CodeAtlasSettings()
    return CodeAtlasSettings(_env_file=env_file)
