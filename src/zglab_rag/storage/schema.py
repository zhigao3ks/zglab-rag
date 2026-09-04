from __future__ import annotations

import json
import sqlite3

from zglab_rag.domain.lexical import DEFAULT_LEXICAL_PROFILE, LexicalProfile

SCHEMA_VERSION = 3
VECTOR_DIMENSION = 512

RELATIONAL_SCHEMA = """
CREATE TABLE schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE index_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE source_snapshots (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    revision TEXT,
    visibility TEXT NOT NULL,
    document_count INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    document_id TEXT UNIQUE NOT NULL,
    source_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    title TEXT NOT NULL,
    visibility TEXT NOT NULL,
    revision TEXT,
    content_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX documents_source_id_idx ON documents(source_id);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    chunk_id TEXT UNIQUE NOT NULL,
    document_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    title TEXT NOT NULL,
    section_path_json TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    visibility TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    revision TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);
CREATE INDEX chunks_document_id_idx ON chunks(document_id);
CREATE INDEX chunks_source_id_idx ON chunks(source_id);
CREATE INDEX chunks_visibility_idx ON chunks(visibility);

CREATE TABLE embedding_profiles (
    profile_id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    dimension INTEGER NOT NULL,
    composition TEXT NOT NULL,
    normalize INTEGER NOT NULL,
    query_mode TEXT NOT NULL,
    max_length INTEGER NOT NULL,
    config_hash TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE chunk_embedding_state (
    chunk_id TEXT PRIMARY KEY,
    embedding_profile_id TEXT NOT NULL,
    embedding_input_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id) ON DELETE CASCADE,
    FOREIGN KEY(embedding_profile_id) REFERENCES embedding_profiles(profile_id)
);
CREATE INDEX chunk_embedding_profile_idx
    ON chunk_embedding_state(embedding_profile_id);

CREATE TABLE index_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    embedding_profile_id TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    new_chunks INTEGER NOT NULL DEFAULT 0,
    changed_chunks INTEGER NOT NULL DEFAULT 0,
    unchanged_chunks INTEGER NOT NULL DEFAULT 0,
    deleted_chunks INTEGER NOT NULL DEFAULT 0,
    embedded_chunks INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY(embedding_profile_id) REFERENCES embedding_profiles(profile_id)
);
CREATE INDEX index_runs_started_at_idx ON index_runs(started_at DESC);
"""

LEXICAL_SCHEMA = """
CREATE TABLE lexical_profiles (
    profile_id TEXT PRIMARY KEY,
    tokenizer TEXT NOT NULL,
    title_weight REAL NOT NULL,
    section_weight REAL NOT NULL,
    content_weight REAL NOT NULL,
    config_version INTEGER NOT NULL,
    config_hash TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL
);
"""

RETRIEVAL_STRUCTURE_VERSION = 1

STRUCTURE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE document_profiles (
        document_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        project_key TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        outline_json TEXT NOT NULL,
        keywords_json TEXT NOT NULL,
        profile_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE sections (
        section_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        section_path_json TEXT NOT NULL,
        section_title TEXT NOT NULL,
        chunk_count INTEGER NOT NULL,
        first_chunk_index INTEGER NOT NULL,
        last_chunk_index INTEGER NOT NULL,
        profile_text TEXT NOT NULL,
        FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX sections_document_idx ON sections(document_id, first_chunk_index)",
    "CREATE INDEX sections_source_idx ON sections(source_id)",
    """
    CREATE VIRTUAL TABLE fts_documents USING fts5(
        document_id UNINDEXED, title, project, locator, summary, outline, keywords,
        tokenize='trigram'
    )
    """,
    """
    CREATE VIRTUAL TABLE fts_sections USING fts5(
        section_id UNINDEXED, document_id UNINDEXED,
        section_title, section_path, profile_text, tokenize='trigram'
    )
    """,
    """
    CREATE TABLE graph_nodes (
        node_id TEXT PRIMARY KEY,
        node_type TEXT NOT NULL CHECK(node_type IN (
            'PROJECT','DOCUMENT','SECTION','PERSON','TECHNOLOGY','TOPIC'
        )),
        canonical_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        metadata_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX graph_nodes_type_name_idx ON graph_nodes(node_type, normalized_name)",
    """
    CREATE TABLE graph_aliases (
        normalized_alias TEXT NOT NULL,
        node_id TEXT NOT NULL,
        PRIMARY KEY(normalized_alias, node_id),
        FOREIGN KEY(node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX graph_aliases_node_idx ON graph_aliases(node_id)",
    """
    CREATE TABLE graph_edges (
        edge_id TEXT PRIMARY KEY,
        source_node_id TEXT NOT NULL,
        relation TEXT NOT NULL CHECK(relation IN (
            'CONTAINS','MENTIONS','RELATED_TO','USES','DEVELOPED','WORKED_ON'
        )),
        target_node_id TEXT NOT NULL,
        provenance_kind TEXT NOT NULL CHECK(provenance_kind IN (
            'STRUCTURAL','CURATED','TEXT_MENTION'
        )),
        source_id TEXT,
        document_id TEXT,
        section_id TEXT,
        chunk_id TEXT,
        metadata_json TEXT NOT NULL,
        FOREIGN KEY(source_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE,
        FOREIGN KEY(target_node_id) REFERENCES graph_nodes(node_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX graph_edges_source_idx ON graph_edges(source_node_id, edge_id)",
    "CREATE INDEX graph_edges_target_idx ON graph_edges(target_node_id, edge_id)",
    "CREATE INDEX graph_edges_document_idx ON graph_edges(document_id)",
)


def _execute_script_atomically(connection: sqlite3.Connection, script: str) -> None:
    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)


def create_schema(connection: sqlite3.Connection) -> None:
    _execute_script_atomically(connection, RELATIONAL_SCHEMA)
    connection.execute(
        "CREATE VIRTUAL TABLE vec_chunks USING vec0("
        f"embedding float[{VECTOR_DIMENSION}] distance_metric=cosine)"
    )
    connection.execute(
        "CREATE VIRTUAL TABLE fts_chunks USING "
        "fts5(title, section_path, content, tokenize='trigram')"
    )
    _execute_script_atomically(connection, LEXICAL_SCHEMA)
    for statement in STRUCTURE_SCHEMA_STATEMENTS:
        connection.execute(statement)
    activate_lexical_profile(connection, DEFAULT_LEXICAL_PROFILE)
    connection.execute(
        "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def create_schema_v1(connection: sqlite3.Connection) -> None:
    """Create the historical schema only for migration tests."""
    _execute_script_atomically(connection, RELATIONAL_SCHEMA)
    connection.execute(
        "CREATE VIRTUAL TABLE vec_chunks USING vec0("
        f"embedding float[{VECTOR_DIMENSION}] distance_metric=cosine)"
    )
    connection.execute(
        "INSERT INTO schema_metadata(key, value) VALUES ('schema_version', '1')"
    )


def activate_lexical_profile(
    connection: sqlite3.Connection,
    profile: LexicalProfile = DEFAULT_LEXICAL_PROFILE,
) -> None:
    connection.execute(
        """
        INSERT INTO lexical_profiles(
            profile_id, tokenizer, title_weight, section_weight, content_weight,
            config_version, config_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(profile_id) DO NOTHING
        """,
        (
            profile.profile_id,
            profile.tokenizer,
            profile.title_weight,
            profile.section_weight,
            profile.content_weight,
            profile.config_version,
            profile.config_hash,
        ),
    )
    connection.execute(
        """
        INSERT INTO index_metadata(key, value) VALUES ('active_lexical_profile_id', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (profile.profile_id,),
    )


def migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(LEXICAL_SCHEMA.strip())
        connection.execute(
            "CREATE VIRTUAL TABLE fts_chunks USING "
            "fts5(title, section_path, content, tokenize='trigram')"
        )
        activate_lexical_profile(connection, DEFAULT_LEXICAL_PROFILE)
        rows = connection.execute(
            "SELECT id, title, section_path_json, content FROM chunks ORDER BY id"
        ).fetchall()
        for row in rows:
            section_path = " > ".join(json.loads(row["section_path_json"]))
            connection.execute(
                "INSERT INTO fts_chunks(rowid, title, section_path, content) "
                "VALUES (?, ?, ?, ?)",
                (row["id"], row["title"], section_path, row["content"]),
            )
        connection.execute(
            "UPDATE schema_metadata SET value='2' WHERE key='schema_version'"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Atomically add all Phase 16 derived retrieval structures."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in STRUCTURE_SCHEMA_STATEMENTS:
            idempotent = statement.replace(
                "CREATE VIRTUAL TABLE ", "CREATE VIRTUAL TABLE IF NOT EXISTS "
            ).replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ").replace(
                "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "
            )
            connection.execute(idempotent)
        connection.execute(
            "UPDATE schema_metadata SET value='3' WHERE key='schema_version'"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
