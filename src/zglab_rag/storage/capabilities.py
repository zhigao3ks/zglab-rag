from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Fts5Capabilities:
    enabled: bool
    trigram: bool
    bm25: bool


def probe_fts5(connection: sqlite3.Connection) -> Fts5Capabilities:
    enabled = bool(
        connection.execute(
            "SELECT sqlite_compileoption_used('ENABLE_FTS5')"
        ).fetchone()[0]
    )
    if not enabled:
        return Fts5Capabilities(enabled=False, trigram=False, bm25=False)
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE temp.fts5_probe USING "
            "fts5(title, content, tokenize='trigram')"
        )
        connection.execute(
            "INSERT INTO fts5_probe(rowid, title, content) VALUES (1, '长期记忆', 'Context')"
        )
        row = connection.execute(
            "SELECT bm25(fts5_probe) FROM fts5_probe "
            "WHERE fts5_probe MATCH '\"长期记忆\"'"
        ).fetchone()
        return Fts5Capabilities(enabled=True, trigram=True, bm25=row is not None)
    except sqlite3.Error:
        return Fts5Capabilities(enabled=True, trigram=False, bm25=False)
    finally:
        connection.execute("DROP TABLE IF EXISTS temp.fts5_probe")
