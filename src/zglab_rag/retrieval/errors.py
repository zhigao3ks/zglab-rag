class RetrievalError(RuntimeError):
    """Base class for retrieval failures."""


class LexicalProfileMismatch(RetrievalError):
    """Raised when the FTS index profile differs from the requested profile."""
