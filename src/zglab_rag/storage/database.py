from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

from zglab_rag.storage.errors import (
    DatabaseInitializationError,
    SchemaVersionError,
    SqliteVecLoadError,
)
from zglab_rag.storage.schema import (
    SCHEMA_VERSION,
    create_schema,
    migrate_v1_to_v2,
    migrate_v2_to_v3,
)

SQLITE_VEC_VERSION = "0.1.9"


@dataclass(frozen=True, slots=True)
class DatabaseVersions:
    sqlite: str
    sqlite_vec: str
    schema: int


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(
        self,
        *,
        read_only: bool = False,
        initialize: bool = True,
        migrate: bool = False,
    ) -> sqlite3.Connection:
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            connection = sqlite3.connect(
                f"file:{self.path.resolve()}?mode=ro", uri=True, isolation_level=None
            )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self._load_sqlite_vec(connection)
        try:
            self._validate_or_initialize(
                connection,
                initialize=initialize and not read_only,
                migrate=migrate and not read_only,
            )
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _load_sqlite_vec(connection: sqlite3.Connection) -> None:
        try:
            connection.enable_load_extension(True)
            sqlite_vec.load(connection)
            raw_version = str(connection.execute("SELECT vec_version()").fetchone()[0])
        except (sqlite3.Error, OSError) as exc:
            raise SqliteVecLoadError(f"Unable to load sqlite-vec extension: {exc}") from exc
        finally:
            try:
                connection.enable_load_extension(False)
            except sqlite3.Error:
                pass
        if raw_version.removeprefix("v") != SQLITE_VEC_VERSION:
            raise SqliteVecLoadError(
                f"sqlite-vec version mismatch: expected {SQLITE_VEC_VERSION}, got {raw_version}"
            )

    @staticmethod
    def _validate_or_initialize(
        connection: sqlite3.Connection,
        *,
        initialize: bool,
        migrate: bool,
    ) -> None:
        has_schema = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
        if not has_schema:
            has_any_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') LIMIT 1"
            ).fetchone()
            if has_any_table or not initialize:
                raise DatabaseInitializationError(
                    "Database is not an initialized ZGLab knowledge index"
                )
            try:
                connection.execute("BEGIN IMMEDIATE")
                create_schema(connection)
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise DatabaseInitializationError(
                    f"Unable to initialize knowledge database: {exc}"
                ) from exc

        row = connection.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            raise SchemaVersionError("Database is missing schema_version")
        try:
            version = int(row[0])
        except (TypeError, ValueError) as exc:
            raise SchemaVersionError(f"Invalid database schema_version: {row[0]!r}") from exc
        if version == 1 and migrate:
            try:
                migrate_v1_to_v2(connection)
            except sqlite3.Error as exc:
                raise DatabaseInitializationError(
                    f"Unable to migrate schema v1 to v2: {exc}"
                ) from exc
            version = 2
        if version == 2 and migrate:
            try:
                migrate_v2_to_v3(connection)
            except sqlite3.Error as exc:
                raise DatabaseInitializationError(
                    f"Unable to migrate schema v2 to v3: {exc}"
                ) from exc
            version = 3
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Unsupported database schema version {version}; expected {SCHEMA_VERSION}. "
                "Run the explicit database migration for an older schema."
            )

    @staticmethod
    def versions(connection: sqlite3.Connection) -> DatabaseVersions:
        vec_version = str(connection.execute("SELECT vec_version()").fetchone()[0])
        schema_version = int(
            connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()[0]
        )
        return DatabaseVersions(
            sqlite=sqlite3.sqlite_version,
            sqlite_vec=vec_version,
            schema=schema_version,
        )
