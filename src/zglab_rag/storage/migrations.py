from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zglab_rag.config import get_settings
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the ZGLab knowledge database")
    parser.add_argument("database", nargs="?", type=Path)
    args = parser.parse_args(argv)
    path = args.database or get_settings().database_path
    database = Database(path)
    if not path.is_file():
        print(f"error: database does not exist: {path}", file=sys.stderr)
        return 1
    try:
        connection = database.connect(initialize=False, migrate=True)
        try:
            counts = IndexRepository(connection).counts()
            fts_count = connection.execute("SELECT count(*) FROM fts_chunks").fetchone()[0]
            versions = database.versions(connection)
            print(f"database: {path}")
            print(f"schema_version: {versions.schema}")
            print(
                f"documents={counts['documents']} chunks={counts['chunks']} "
                f"vectors={counts['vectors']} fts_rows={fts_count}"
            )
        finally:
            connection.close()
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
