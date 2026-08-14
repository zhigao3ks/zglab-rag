class IngestionError(ValueError):
    """Base error for invalid ingestible content."""


class MalformedFrontmatterError(IngestionError):
    """Raised when YAML frontmatter cannot be parsed as a mapping."""


class EmptyDocumentError(IngestionError):
    """Raised when a Markdown document has no body."""


class MissingMetadataError(IngestionError):
    """Raised when required document metadata cannot be resolved."""


class InvalidMetadataError(IngestionError):
    """Raised when document metadata has an invalid value or type."""
