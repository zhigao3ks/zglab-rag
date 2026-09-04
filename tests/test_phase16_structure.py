from __future__ import annotations

import sqlite3

import pytest

from tests.test_lexical_hybrid import FakeEmbeddingProvider, _profile, _source_input
from zglab_rag.indexing.indexer import KnowledgeIndexer
from zglab_rag.indexing.models import SourceIndexInput
from zglab_rag.knowledge_structure.builder import (
    normalize_name,
    rebuild_knowledge_structure,
    section_id_for,
)
from zglab_rag.storage import schema
from zglab_rag.storage.database import Database
from zglab_rag.storage.errors import DatabaseInitializationError, SchemaVersionError


def test_fresh_knowledge_database_has_atomic_v3_structure(tmp_path) -> None:
    connection = Database(tmp_path / "fresh.db").connect()
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )
    }
    assert Database.versions(connection).schema == 3
    assert {
        "document_profiles",
        "sections",
        "fts_documents",
        "fts_sections",
        "graph_nodes",
        "graph_aliases",
        "graph_edges",
    } <= names


def _downgrade_empty_v3_to_v2(connection: sqlite3.Connection) -> None:
    for table in (
        "fts_documents",
        "fts_sections",
        "graph_edges",
        "graph_aliases",
        "graph_nodes",
        "sections",
        "document_profiles",
    ):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("UPDATE schema_metadata SET value='2' WHERE key='schema_version'")


def test_v2_to_v3_migration_preserves_core_index(tmp_path) -> None:
    database = Database(tmp_path / "migration.db")
    connection = database.connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_source_input("notes", [("mcp", "MCP protocol")])]
    )
    expected = {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("documents", "chunks", "vec_chunks", "fts_chunks", "index_runs")
    }
    _downgrade_empty_v3_to_v2(connection)
    connection.close()
    migrated = database.connect(initialize=False, migrate=True)
    assert Database.versions(migrated).schema == 3
    assert expected == {
        table: migrated.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in expected
    }


def test_v2_to_v3_failure_is_fully_atomic(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "failure.db")
    connection = database.connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_source_input("notes", [("mcp", "MCP protocol")])]
    )
    _downgrade_empty_v3_to_v2(connection)
    core_count = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
    connection.close()
    monkeypatch.setattr(
        schema,
        "STRUCTURE_SCHEMA_STATEMENTS",
        (
            schema.STRUCTURE_SCHEMA_STATEMENTS[0],
            "CREATE TABLE forced_failure(value TEXT, broken REFERENCES missing(",
        ),
    )
    with pytest.raises(DatabaseInitializationError):
        database.connect(initialize=False, migrate=True)
    raw = sqlite3.connect(database.path)
    assert raw.execute(
        "SELECT value FROM schema_metadata WHERE key='schema_version'"
    ).fetchone()[0] == "2"
    assert raw.execute(
        "SELECT 1 FROM sqlite_master WHERE name='document_profiles'"
    ).fetchone() is None
    assert raw.execute("SELECT count(*) FROM chunks").fetchone()[0] == core_count
    raw.close()


def test_fresh_v3_initialization_failure_leaves_no_partial_schema(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "fresh-failure.db")
    monkeypatch.setattr(
        schema,
        "STRUCTURE_SCHEMA_STATEMENTS",
        (schema.STRUCTURE_SCHEMA_STATEMENTS[0], "CREATE TABLE broken(value TEXT,"),
    )
    with pytest.raises(DatabaseInitializationError):
        database.connect()
    raw = sqlite3.connect(database.path)
    assert raw.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    raw.close()


def test_future_schema_version_fails_closed(tmp_path) -> None:
    database = Database(tmp_path / "future.db")
    connection = database.connect()
    connection.execute("UPDATE schema_metadata SET value='99'")
    connection.close()
    with pytest.raises(SchemaVersionError):
        database.connect(initialize=False, migrate=True)


def test_profiles_sections_and_graph_are_deterministic_and_public(tmp_path) -> None:
    connection = Database(tmp_path / "structure.db").connect()
    indexer = KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile())
    indexer.build(
        [
            _source_input("notes", [("mcp", "MCP and Model Context Protocol")]),
            _source_input(
                "private", [("secret", "MCP private")], visibility="private"
            ),
        ]
    )
    before = connection.execute(
        "SELECT profile_hash FROM document_profiles WHERE source_id='notes'"
    ).fetchone()[0]
    rebuild_knowledge_structure(connection, run_id="same")
    first = connection.execute(
        "SELECT profile_hash FROM document_profiles WHERE source_id='notes'"
    ).fetchone()[0]
    rebuild_knowledge_structure(connection, run_id="same")
    second = connection.execute(
        "SELECT profile_hash FROM document_profiles WHERE source_id='notes'"
    ).fetchone()[0]
    assert before == first == second
    assert connection.execute("SELECT count(*) FROM sections").fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM document_profiles WHERE source_id='private'"
    ).fetchone()[0] == 0
    edge = connection.execute(
        "SELECT * FROM graph_edges WHERE relation='MENTIONS'"
    ).fetchone()
    assert edge["provenance_kind"] == "TEXT_MENTION" and edge["chunk_id"] == "mcp"
    assert connection.execute(
        "SELECT count(*) FROM graph_edges WHERE relation='USES'"
    ).fetchone()[0] == 0


def test_invalid_curated_provenance_is_rejected(tmp_path) -> None:
    connection = Database(tmp_path / "curated.db").connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_source_input("notes", [("mcp", "MCP protocol")])]
    )
    catalog = tmp_path / "graph.yaml"
    catalog.write_text(
        """
version: 1
entities:
  - {id: technology:mcp, type: TECHNOLOGY, name: MCP}
relations:
  - source: project:notes
    relation: USES
    target: technology:mcp
    chunk_id: missing
""",
        encoding="utf-8",
    )
    rebuild_knowledge_structure(connection, run_id="curated", catalog_path=catalog)
    assert connection.execute(
        "SELECT count(*) FROM graph_edges WHERE provenance_kind='CURATED'"
    ).fetchone()[0] == 0


def test_deleted_document_removes_all_derived_structure(tmp_path) -> None:
    connection = Database(tmp_path / "delete.db").connect()
    source = _source_input("notes", [("mcp", "MCP protocol")])
    indexer = KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile())
    indexer.build([source])
    empty = SourceIndexInput(
        source=source.source, revision="revision-2", documents=[], chunks=[]
    )
    indexer.build([empty])
    for table in ("documents", "document_profiles", "sections", "graph_edges"):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [(" ＭＣＰ  Protocol ", "mcp protocol"), ("AI\tAgent", "ai agent")],
)
def test_entity_normalization_is_nfkc_and_whitespace_bounded(value, expected) -> None:
    assert normalize_name(value) == expected


def test_section_id_is_deterministic_and_document_scoped() -> None:
    assert section_id_for("a", ["Root"]) == section_id_for("a", ["Root"])
    assert section_id_for("a", ["Root"]) != section_id_for("b", ["Root"])
