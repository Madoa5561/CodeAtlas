"""Safe, framework-independent exception types used across CodeAtlas."""

from __future__ import annotations


class CodeAtlasError(Exception):
    """Base error with separate public text and an internal cause."""

    default_safe_message = "CodeAtlas could not complete the request."
    code = "codeatlas_error"

    def __init__(
        self,
        safe_message: str | None = None,
        *,
        internal_cause: BaseException | None = None,
    ) -> None:
        self.safe_message = safe_message or self.default_safe_message
        self.internal_cause = internal_cause
        super().__init__(self.safe_message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(safe_message={self.safe_message!r})"


class ConfigurationError(CodeAtlasError):
    """Configuration could not be loaded or validated."""

    default_safe_message = "The application configuration is invalid."
    code = "configuration_error"


class InputValidationError(CodeAtlasError):
    """Input failed application-level validation."""

    default_safe_message = "The supplied input is invalid."
    code = "validation_error"


class NotFoundError(CodeAtlasError):
    """A requested resource was not found."""

    default_safe_message = "The requested resource was not found."
    code = "not_found"


class ForbiddenError(CodeAtlasError):
    """An operation was denied by policy."""

    default_safe_message = "This operation is not permitted."
    code = "forbidden"


class RequestTimeoutError(CodeAtlasError):
    """An operation exceeded its configured deadline."""

    default_safe_message = "The operation timed out."
    code = "timeout"


class RateLimitedError(CodeAtlasError):
    """An upstream or local rate limit was reached."""

    default_safe_message = "The service is temporarily rate limited."
    code = "rate_limited"


class ProviderError(CodeAtlasError):
    """An external provider failed without exposing provider details."""

    default_safe_message = "An external service is temporarily unavailable."
    code = "provider_error"


class RepositoryError(CodeAtlasError):
    """A repository operation failed."""

    default_safe_message = "The repository operation failed."
    code = "repository_error"


class IndexingError(CodeAtlasError):
    """A repository indexing operation failed."""

    default_safe_message = "The indexing operation failed."
    code = "indexing_error"


class IndexBusyError(IndexingError):
    """Another indexing lease already owns the repository."""

    default_safe_message = "The repository is already being indexed."
    code = "index_busy"


class IndexSchemaError(IndexingError):
    """The persistent index schema is not at the required revision."""

    default_safe_message = "The index database schema is not current."
    code = "index_schema_error"


class IndexValidationError(IndexingError):
    """A staging generation failed consistency validation."""

    default_safe_message = "The index generation failed validation."
    code = "index_validation_error"


class SecurityError(CodeAtlasError):
    """A security policy rejected an operation."""

    default_safe_message = "The operation was rejected by security policy."
    code = "security_error"


class BinaryFileError(SecurityError):
    """A binary file was rejected from a text-only operation."""

    default_safe_message = "The requested file is not approved text content."
    code = "binary_file"


class SecretDetectedError(SecurityError):
    """A possible secret caused content access to be denied."""

    default_safe_message = "The requested content is excluded by security policy."
    code = "secret_detected"


class LimitExceededError(CodeAtlasError):
    """A configured resource or output limit was exceeded."""

    default_safe_message = "The operation exceeded a configured safety limit."
    code = "limit_exceeded"


class InvalidRevisionError(InputValidationError):
    """A revision was invalid, ambiguous, or did not identify a commit."""

    default_safe_message = "The requested revision is invalid or unavailable."
    code = "invalid_revision"
