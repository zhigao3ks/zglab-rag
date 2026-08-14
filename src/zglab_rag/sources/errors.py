class SourceError(ValueError):
    """Base error for source configuration, discovery, and reading failures."""


class SourceConfigurationError(SourceError):
    """Raised when a registered source is incomplete or unsafe."""


class SourcePathNotFoundError(SourceError):
    """Raised when a configured local source path does not exist."""


class NotGitRepositoryError(SourceError):
    """Raised when a configured Git source is not a repository root."""


class RepositoryMismatchError(SourceError):
    """Raised when a local origin does not match the configured repository."""


class SourceReadError(SourceError):
    """Raised when an allowed source document cannot be read."""


class SourceNotRegisteredError(SourceError):
    """Raised when a caller requests an unregistered or disabled source."""


class LocalSourceError(SourceError):
    """Backward-compatible local curated source error."""
