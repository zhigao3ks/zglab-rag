class StorageError(RuntimeError):
    """Base class for persistent index storage failures."""


class SqliteVecLoadError(StorageError):
    """Raised when sqlite-vec cannot be loaded or has an unexpected version."""


class DatabaseInitializationError(StorageError):
    """Raised when the database cannot be initialized safely."""


class SchemaVersionError(StorageError):
    """Raised when a database schema is unsupported."""
