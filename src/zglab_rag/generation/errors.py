class GenerationError(RuntimeError):
    """Base class for grounded generation failures."""


class ProviderNotConfigured(GenerationError):
    """Raised when no LLM provider configuration is available."""


class RetrievalFailure(GenerationError):
    """Raised when the retrieval step fails before generation."""


class InsufficientEvidence(GenerationError):
    """Raised when no safe grounded answer can be produced."""


class ProviderFailure(GenerationError):
    """Raised when the generation provider fails (network, timeout, HTTP)."""


class InvalidStructuredOutput(GenerationError):
    """Raised when the provider response cannot be parsed into the schema."""


class CitationValidationFailure(GenerationError):
    """Raised when generated citations violate deterministic grounding rules."""
