class IndexingError(RuntimeError):
    """Base class for knowledge index lifecycle failures."""


class IndexProfileMismatch(IndexingError):
    """Raised when writes would mix incompatible embedding profiles."""


class RebuildScopeError(IndexingError):
    """Raised when a profile-changing rebuild omits indexed sources."""


class EmbeddingValidationError(IndexingError):
    """Raised when an embedding provider returns invalid vectors."""
