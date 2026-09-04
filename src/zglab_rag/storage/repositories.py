from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import numpy as np
from sqlite_vec import serialize_float32

from zglab_rag.domain.lexical import LexicalProfile
from zglab_rag.domain.models import KnowledgeChunk, KnowledgeDocument, Visibility
from zglab_rag.indexing.models import (
    EmbeddingProfile,
    IndexPlan,
    SourceIndexInput,
    StoredChunkState,
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class IndexRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def active_profile_id(self) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM index_metadata WHERE key='active_embedding_profile_id'"
        ).fetchone()
        return None if row is None else str(row[0])

    def profile(self, profile_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM embedding_profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()

    def active_lexical_profile_id(self) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM index_metadata WHERE key='active_lexical_profile_id'"
        ).fetchone()
        return None if row is None else str(row[0])

    def lexical_profile(self, profile_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM lexical_profiles WHERE profile_id=?", (profile_id,)
        ).fetchone()

    def validate_lexical_profile(self, profile: LexicalProfile) -> None:
        active = self.active_lexical_profile_id()
        if active != profile.profile_id:
            raise RuntimeError(
                "Lexical profile mismatch: "
                f"database={active}, requested={profile.profile_id}"
            )

    def store_profile(self, profile: EmbeddingProfile) -> None:
        self.connection.execute(
            """
            INSERT INTO embedding_profiles(
                profile_id, model_id, model_name, dimension, composition, normalize,
                query_mode, max_length, config_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO NOTHING
            """,
            (
                profile.profile_id,
                profile.model_id,
                profile.model_name,
                profile.dimension,
                profile.composition.value,
                int(profile.normalize),
                profile.query_mode,
                profile.max_length,
                profile.config_hash,
                utc_now(),
            ),
        )

    def stored_chunk_states(self, source_ids: Sequence[str]) -> list[StoredChunkState]:
        if not source_ids:
            return []
        placeholders = ",".join("?" for _ in source_ids)
        rows = self.connection.execute(
            f"""
            SELECT c.chunk_id, c.source_id, s.embedding_profile_id, s.embedding_input_hash
            FROM chunks c
            LEFT JOIN chunk_embedding_state s ON s.chunk_id = c.chunk_id
            WHERE c.source_id IN ({placeholders})
            """,
            tuple(source_ids),
        ).fetchall()
        return [
            StoredChunkState(
                chunk_id=row["chunk_id"],
                source_id=row["source_id"],
                embedding_profile_id=row["embedding_profile_id"],
                embedding_input_hash=row["embedding_input_hash"],
            )
            for row in rows
        ]

    def indexed_source_ids(self) -> set[str]:
        return {
            str(row[0])
            for row in self.connection.execute("SELECT source_id FROM source_snapshots")
        }

    def begin_run(
        self,
        profile: EmbeddingProfile,
        sources: Sequence[SourceIndexInput],
        plan: IndexPlan,
    ) -> str:
        run_id = str(uuid.uuid4())
        source_snapshot = [
            {
                "source_id": item.source.id,
                "source_kind": item.source.kind.value,
                "revision": item.revision,
                "visibility": item.source.visibility.value,
                "document_count": len(item.documents),
                "chunk_count": len(item.chunks),
            }
            for item in sources
        ]
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.store_profile(profile)
            self.connection.execute(
                """
                INSERT INTO index_runs(
                    run_id, status, started_at, embedding_profile_id, source_snapshot_json,
                    total_chunks, new_chunks, changed_chunks, unchanged_chunks, deleted_chunks
                ) VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    utc_now(),
                    profile.profile_id,
                    json.dumps(source_snapshot, ensure_ascii=False, sort_keys=True),
                    plan.total,
                    len(plan.new),
                    len(plan.changed),
                    len(plan.unchanged),
                    len(plan.deleted),
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return run_id

    def mark_run_failed(self, run_id: str, error: Exception) -> None:
        self.connection.execute(
            """
            UPDATE index_runs
            SET status='failed', finished_at=?, error_message=?
            WHERE run_id=?
            """,
            (utc_now(), f"{type(error).__name__}: {error}", run_id),
        )

    def apply(
        self,
        *,
        run_id: str,
        profile: EmbeddingProfile,
        sources: Sequence[SourceIndexInput],
        plan: IndexPlan,
        embeddings: Mapping[str, np.ndarray],
        reset_all_vectors: bool,
    ) -> None:
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            if reset_all_vectors:
                self.connection.execute("DELETE FROM vec_chunks")
                self.connection.execute("DELETE FROM chunk_embedding_state")

            for source_input in sources:
                self._upsert_source(source_input, now)
                current_document_ids = {doc.document_id for doc in source_input.documents}
                for document in source_input.documents:
                    self._upsert_document(document, now)
                for chunk in source_input.chunks:
                    self._upsert_chunk(chunk, now)

                self._delete_stale_chunks(source_input.source.id, plan)
                self._delete_stale_documents(source_input.source.id, current_document_ids)

            for item in plan.needs_embedding:
                chunk_row = self.connection.execute(
                    "SELECT id FROM chunks WHERE chunk_id=?", (item.chunk.chunk_id,)
                ).fetchone()
                if chunk_row is None:
                    raise RuntimeError(f"Chunk was not persisted: {item.chunk.chunk_id}")
                row_id = int(chunk_row[0])
                self.connection.execute("DELETE FROM fts_chunks WHERE rowid=?", (row_id,))
                self.connection.execute(
                    "INSERT INTO fts_chunks(rowid, title, section_path, content) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        row_id,
                        item.chunk.title,
                        " > ".join(item.chunk.section_path),
                        item.chunk.content,
                    ),
                )

            for item in plan.needs_embedding:
                chunk_row = self.connection.execute(
                    "SELECT id FROM chunks WHERE chunk_id=?", (item.chunk.chunk_id,)
                ).fetchone()
                if chunk_row is None:
                    raise RuntimeError(f"Chunk was not persisted: {item.chunk.chunk_id}")
                row_id = int(chunk_row[0])
                self.connection.execute("DELETE FROM vec_chunks WHERE rowid=?", (row_id,))
                self.connection.execute(
                    "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (row_id, serialize_float32(embeddings[item.chunk.chunk_id])),
                )
                self.connection.execute(
                    """
                    INSERT INTO chunk_embedding_state(
                        chunk_id, embedding_profile_id, embedding_input_hash, indexed_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        embedding_profile_id=excluded.embedding_profile_id,
                        embedding_input_hash=excluded.embedding_input_hash,
                        indexed_at=excluded.indexed_at
                    """,
                    (
                        item.chunk.chunk_id,
                        profile.profile_id,
                        item.embedding_input_hash,
                        now,
                    ),
                )

            self.connection.execute(
                """
                INSERT INTO index_metadata(key, value)
                VALUES ('active_embedding_profile_id', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (profile.profile_id,),
            )
            # Derived hierarchy and graph are rebuilt inside the same core
            # transaction. A failure therefore cannot publish a completed run
            # with stale or partial retrieval structures.
            from zglab_rag.knowledge_structure.builder import rebuild_knowledge_structure

            rebuild_knowledge_structure(self.connection, run_id=run_id)
            self.connection.execute(
                """
                UPDATE index_runs
                SET status='completed', finished_at=?, embedded_chunks=?
                WHERE run_id=?
                """,
                (now, len(embeddings), run_id),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _upsert_source(self, item: SourceIndexInput, now: str) -> None:
        self.connection.execute(
            """
            INSERT INTO source_snapshots(
                source_id, source_kind, revision, visibility,
                document_count, chunk_count, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                source_kind=excluded.source_kind,
                revision=excluded.revision,
                visibility=excluded.visibility,
                document_count=excluded.document_count,
                chunk_count=excluded.chunk_count,
                indexed_at=excluded.indexed_at
            """,
            (
                item.source.id,
                item.source.kind.value,
                item.revision,
                item.source.visibility.value,
                len(item.documents),
                len(item.chunks),
                now,
            ),
        )

    def _upsert_document(self, document: KnowledgeDocument, now: str) -> None:
        metadata = document.model_dump(mode="json", exclude={"content"})
        self.connection.execute(
            """
            INSERT INTO documents(
                document_id, source_id, source_path, title, visibility, revision,
                content_hash, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                source_id=excluded.source_id,
                source_path=excluded.source_path,
                title=excluded.title,
                visibility=excluded.visibility,
                revision=excluded.revision,
                content_hash=excluded.content_hash,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                document.document_id,
                document.source_id,
                document.path,
                document.title,
                document.visibility.value,
                document.source_revision,
                document.content_hash,
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )

    def _upsert_chunk(self, chunk: KnowledgeChunk, now: str) -> None:
        self.connection.execute(
            """
            INSERT INTO chunks(
                chunk_id, document_id, source_id, source_path, title,
                section_path_json, chunk_index, visibility, content,
                content_hash, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                document_id=excluded.document_id,
                source_id=excluded.source_id,
                source_path=excluded.source_path,
                title=excluded.title,
                section_path_json=excluded.section_path_json,
                chunk_index=excluded.chunk_index,
                visibility=excluded.visibility,
                content=excluded.content,
                content_hash=excluded.content_hash,
                revision=excluded.revision,
                updated_at=excluded.updated_at
            """,
            (
                chunk.chunk_id,
                chunk.document_id,
                chunk.source_id,
                chunk.source_path,
                chunk.title,
                json.dumps(chunk.section_path, ensure_ascii=False),
                chunk.chunk_index,
                chunk.visibility.value,
                chunk.content,
                chunk.content_hash,
                chunk.revision,
                now,
                now,
            ),
        )

    def _delete_stale_chunks(self, source_id: str, plan: IndexPlan) -> None:
        stale_ids = [item.chunk_id for item in plan.deleted if item.source_id == source_id]
        for chunk_id in stale_ids:
            row = self.connection.execute(
                "SELECT id FROM chunks WHERE chunk_id=?", (chunk_id,)
            ).fetchone()
            if row is not None:
                self.connection.execute("DELETE FROM vec_chunks WHERE rowid=?", (int(row[0]),))
                self.connection.execute(
                    "DELETE FROM fts_chunks WHERE rowid=?", (int(row[0]),)
                )
            self.connection.execute(
                "DELETE FROM chunk_embedding_state WHERE chunk_id=?", (chunk_id,)
            )
            self.connection.execute("DELETE FROM chunks WHERE chunk_id=?", (chunk_id,))

    def _delete_stale_documents(self, source_id: str, current_ids: set[str]) -> None:
        rows = self.connection.execute(
            "SELECT document_id FROM documents WHERE source_id=?", (source_id,)
        ).fetchall()
        for row in rows:
            if row[0] not in current_ids:
                self.connection.execute("DELETE FROM documents WHERE document_id=?", (row[0],))

    def counts(self) -> dict[str, int]:
        def count(table: str) -> int:
            allowed = {"source_snapshots", "documents", "chunks", "vec_chunks"}
            if table not in allowed:
                raise ValueError(f"Unsupported count table: {table}")
            return int(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

        return {
            "sources": count("source_snapshots"),
            "documents": count("documents"),
            "chunks": count("chunks"),
            "vectors": count("vec_chunks"),
        }

    def source_snapshots(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM source_snapshots ORDER BY source_id"
        ).fetchall()

    def last_run(self) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM index_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    def public_vector_search(
        self,
        query_vector: np.ndarray,
        *,
        top_k: int,
    ) -> list[sqlite3.Row]:
        if not self.connection.execute("SELECT 1 FROM vec_chunks LIMIT 1").fetchone():
            return []
        candidates = self.connection.execute(
            """
            SELECT rowid, distance
            FROM vec_chunks
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (serialize_float32(query_vector), top_k),
        ).fetchall()
        results: list[sqlite3.Row] = []
        for candidate in candidates:
            row = self.connection.execute(
                """
                SELECT c.*, ? AS distance
                FROM chunks c
                WHERE c.id=? AND c.visibility=?
                """,
                (float(candidate["distance"]), int(candidate["rowid"]), Visibility.PUBLIC.value),
            ).fetchone()
            if row is not None:
                results.append(row)
                if len(results) == top_k:
                    break
        return results

    def vector_count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM vec_chunks").fetchone()[0])

    def lexical_count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM fts_chunks").fetchone()[0])

    def vector_candidates(
        self,
        query_vector: np.ndarray,
        *,
        candidate_k: int,
    ) -> list[sqlite3.Row]:
        if candidate_k <= 0 or self.vector_count() == 0:
            return []
        return self.connection.execute(
            """
            SELECT rowid, distance
            FROM vec_chunks
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance
            """,
            (serialize_float32(query_vector), candidate_k),
        ).fetchall()

    def hydrate_filtered_candidates(
        self,
        row_ids: Sequence[int],
        *,
        visibility: str,
        source_ids: Sequence[str] = (),
        scopes: Sequence[str] = (),
        document_ids: Sequence[str] = (),
        section_ids: Sequence[str] = (),
    ) -> dict[int, sqlite3.Row]:
        """Hydrate only allowed rows so filtered metadata never leaves SQLite."""
        if not row_ids:
            return {}
        clauses = ["c.visibility = ?"]
        parameters: list[object] = [visibility]
        if source_ids:
            clauses.append(f"c.source_id IN ({','.join('?' for _ in source_ids)})")
            parameters.extend(source_ids)
        if scopes:
            clauses.append(
                f"json_extract(d.metadata_json, '$.scope') IN "
                f"({','.join('?' for _ in scopes)})"
            )
            parameters.extend(scopes)
        if document_ids:
            clauses.append(f"c.document_id IN ({','.join('?' for _ in document_ids)})")
            parameters.extend(document_ids)
        if section_ids:
            clauses.append(
                "EXISTS(SELECT 1 FROM sections s WHERE s.document_id=c.document_id "
                "AND s.section_path_json=c.section_path_json "
                f"AND s.section_id IN ({','.join('?' for _ in section_ids)}))"
            )
            parameters.extend(section_ids)
        row_placeholders = ",".join("?" for _ in row_ids)
        clauses.append(f"c.id IN ({row_placeholders})")
        parameters.extend(row_ids)
        rows = self.connection.execute(
            f"""
            SELECT c.*, json_extract(d.metadata_json, '$.scope') AS scope
            FROM chunks c
            JOIN documents d ON d.document_id = c.document_id
            WHERE {' AND '.join(clauses)}
            """,
            tuple(parameters),
        ).fetchall()
        return {int(row["id"]): row for row in rows}

    def lexical_search(
        self,
        match_expression: str,
        *,
        top_k: int,
        visibility: str,
        title_weight: float,
        section_weight: float,
        content_weight: float,
        source_ids: Sequence[str] = (),
        scopes: Sequence[str] = (),
        document_ids: Sequence[str] = (),
        section_ids: Sequence[str] = (),
    ) -> list[sqlite3.Row]:
        clauses = ["fts_chunks MATCH ?", "c.visibility = ?"]
        filter_parameters: list[object] = [match_expression, visibility]
        if source_ids:
            clauses.append(f"c.source_id IN ({','.join('?' for _ in source_ids)})")
            filter_parameters.extend(source_ids)
        if scopes:
            clauses.append(
                f"json_extract(d.metadata_json, '$.scope') IN "
                f"({','.join('?' for _ in scopes)})"
            )
            filter_parameters.extend(scopes)
        if document_ids:
            clauses.append(f"c.document_id IN ({','.join('?' for _ in document_ids)})")
            filter_parameters.extend(document_ids)
        if section_ids:
            clauses.append(
                "EXISTS(SELECT 1 FROM sections s WHERE s.document_id=c.document_id "
                "AND s.section_path_json=c.section_path_json "
                f"AND s.section_id IN ({','.join('?' for _ in section_ids)}))"
            )
            filter_parameters.extend(section_ids)
        parameters: list[object] = [title_weight, section_weight, content_weight]
        parameters.extend(filter_parameters)
        parameters.append(top_k)
        return self.connection.execute(
            f"""
            SELECT c.*, json_extract(d.metadata_json, '$.scope') AS scope,
                   bm25(fts_chunks, ?, ?, ?) AS raw_bm25
            FROM fts_chunks
            JOIN chunks c ON c.id = fts_chunks.rowid
            JOIN documents d ON d.document_id = c.document_id
            WHERE {' AND '.join(clauses)}
            ORDER BY raw_bm25 ASC, c.chunk_id ASC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()

    def search_document_profiles(
        self,
        match_expression: str,
        *,
        top_k: int,
        source_ids: Sequence[str] = (),
        document_ids: Sequence[str] = (),
    ) -> list[sqlite3.Row]:
        clauses = ["fts_documents MATCH ?"]
        parameters: list[object] = [match_expression]
        if source_ids:
            clauses.append(f"d.source_id IN ({','.join('?' for _ in source_ids)})")
            parameters.extend(source_ids)
        if document_ids:
            clauses.append(f"d.document_id IN ({','.join('?' for _ in document_ids)})")
            parameters.extend(document_ids)
        parameters.append(top_k)
        return self.connection.execute(
            f"""
            SELECT d.*, dp.project_key,
                   bm25(fts_documents, 0.0, 3.0, 3.0, 1.0, 1.5, 2.0, 2.5) AS raw_bm25
            FROM fts_documents
            JOIN documents d ON d.document_id=fts_documents.document_id
            JOIN document_profiles dp ON dp.document_id=d.document_id
            WHERE d.visibility='public' AND {' AND '.join(clauses)}
            ORDER BY raw_bm25,d.document_id LIMIT ?
            """,
            parameters,
        ).fetchall()

    def search_sections(
        self,
        match_expression: str,
        *,
        document_ids: Sequence[str],
        top_k: int,
    ) -> list[sqlite3.Row]:
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        return self.connection.execute(
            f"""
            SELECT s.*,bm25(fts_sections,0.0,0.0,3.0,2.0,1.0) AS raw_bm25
            FROM fts_sections JOIN sections s ON s.section_id=fts_sections.section_id
            JOIN documents d ON d.document_id=s.document_id
            WHERE fts_sections MATCH ? AND s.document_id IN ({placeholders})
              AND d.visibility='public'
            ORDER BY raw_bm25,s.section_id LIMIT ?
            """,
            (match_expression, *document_ids, top_k),
        ).fetchall()

    def public_chunks_by_ids(
        self, chunk_ids: Sequence[str], *, filters
    ) -> dict[str, sqlite3.Row]:
        if not chunk_ids:
            return {}
        rows = self.connection.execute(
            f"SELECT id,chunk_id FROM chunks WHERE chunk_id IN "
            f"({','.join('?' for _ in chunk_ids)})",
            tuple(chunk_ids),
        ).fetchall()
        by_row = self.hydrate_filtered_candidates(
            [row["id"] for row in rows],
            visibility=filters.visibility.value,
            source_ids=filters.source_ids,
            scopes=[scope.value for scope in filters.scopes],
            document_ids=filters.document_ids,
            section_ids=filters.section_ids,
        )
        return {row["chunk_id"]: row for row in by_row.values()}
