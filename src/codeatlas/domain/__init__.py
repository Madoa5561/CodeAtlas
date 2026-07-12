"""Framework-independent domain contracts for CodeAtlas."""

from codeatlas.domain.errors import (
    BinaryFileError,
    CodeAtlasError,
    ConfigurationError,
    ForbiddenError,
    IndexingError,
    InputValidationError,
    InvalidRevisionError,
    LimitExceededError,
    NotFoundError,
    ProviderError,
    RateLimitedError,
    RepositoryError,
    RequestTimeoutError,
    SecretDetectedError,
    SecurityError,
)

__all__ = [
    "BinaryFileError",
    "CodeAtlasError",
    "ConfigurationError",
    "ForbiddenError",
    "IndexingError",
    "InputValidationError",
    "InvalidRevisionError",
    "LimitExceededError",
    "NotFoundError",
    "ProviderError",
    "RateLimitedError",
    "RepositoryError",
    "RequestTimeoutError",
    "SecretDetectedError",
    "SecurityError",
]
