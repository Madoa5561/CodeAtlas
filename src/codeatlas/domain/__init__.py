"""Framework-independent domain contracts for CodeAtlas."""

from codeatlas.domain.errors import (
    CodeAtlasError,
    ConfigurationError,
    ForbiddenError,
    IndexingError,
    InputValidationError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    RepositoryError,
    RequestTimeoutError,
    SecurityError,
)

__all__ = [
    "CodeAtlasError",
    "ConfigurationError",
    "ForbiddenError",
    "IndexingError",
    "InputValidationError",
    "NotFoundError",
    "ProviderError",
    "RateLimitedError",
    "RepositoryError",
    "RequestTimeoutError",
    "SecurityError",
]
