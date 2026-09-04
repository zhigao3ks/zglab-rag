from __future__ import annotations

import json
import sqlite3
from collections import deque
from time import perf_counter

from pydantic import BaseModel

from zglab_rag.knowledge_structure.builder import normalize_name
from zglab_rag.retrieval.config import GraphRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.lexical import LexicalRetriever
from zglab_rag.storage.repositories import IndexRepository


class GraphDiagnostics(BaseModel):
    matched_node_ids: tuple[str, ...]
    graph_nodes_visited: int
    graph_edges_visited: int
    graph_candidate_documents: int
    graph_candidate_sections: int
    provenance_chunk_count: int
    returned_count: int
    total_retrieval_latency_ms: float
    top_k: int
    filters: RetrievalFilter


class GraphResponse(BaseModel):
    results: list[RetrievalResult]
    diagnostics: GraphDiagnostics


class GraphRetriever:
    """Bounded relational traversal which resolves only real PUBLIC chunks."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lexical_retriever: LexicalRetriever,
        *,
        config: GraphRetrievalConfig | None = None,
    ) -> None:
        self.connection = connection
        self.repository = IndexRepository(connection)
        self.lexical = lexical_retriever
        self.config = config or GraphRetrievalConfig()

    @property
    def corpus_size(self) -> int:
        return self.lexical.corpus_size

    def resolve_entities(self, text: str) -> tuple[str, ...]:
        normalized = normalize_name(text)
        rows = self.connection.execute(
            "SELECT a.normalized_alias,a.node_id FROM graph_aliases a "
            "JOIN graph_nodes n ON n.node_id=a.node_id "
            "ORDER BY length(a.normalized_alias) DESC,"
            "CASE n.node_type WHEN 'PERSON' THEN 0 WHEN 'TECHNOLOGY' THEN 1 "
            "WHEN 'TOPIC' THEN 2 ELSE 3 END,a.normalized_alias,a.node_id"
        ).fetchall()
        matches: list[str] = []
        occupied: list[tuple[int, int]] = []
        for row in rows:
            alias = row["normalized_alias"]
            start = normalized.find(alias)
            if start < 0:
                continue
            span = (start, start + len(alias))
            if any(span[0] < end and begin < span[1] for begin, end in occupied):
                continue
            if row["node_id"] not in matches:
                matches.append(row["node_id"])
                occupied.append(span)
                if len(matches) == self.config.max_start_nodes:
                    break
        return tuple(matches)

    def retrieve(self, query: RetrievalQuery) -> GraphResponse:
        started = perf_counter()
        top_k = self.config.validate_top_k(query.top_k)
        starts = self.resolve_entities(query.text)
        if not starts:
            return self._response(query, [], starts, 0, 0, (), (), (), started, top_k)

        queue = deque((node_id, 0) for node_id in starts)
        visited = set(starts)
        edges: list[sqlite3.Row] = []
        documents: list[str] = []
        sections: list[str] = []
        chunks: list[str] = []
        while queue and len(visited) <= self.config.max_nodes:
            node_id, hop = queue.popleft()
            if hop >= self.config.max_hops:
                continue
            rows = self.connection.execute(
                "SELECT * FROM graph_edges WHERE source_node_id=? OR target_node_id=? "
                "ORDER BY edge_id",
                (node_id, node_id),
            ).fetchall()
            for edge in rows:
                if len(edges) == self.config.max_edges:
                    queue.clear()
                    break
                edges.append(edge)
                if edge["document_id"] and edge["document_id"] not in documents:
                    if len(documents) < self.config.max_candidate_documents:
                        documents.append(edge["document_id"])
                if edge["section_id"] and edge["section_id"] not in sections:
                    sections.append(edge["section_id"])
                if edge["chunk_id"] and edge["chunk_id"] not in chunks:
                    chunks.append(edge["chunk_id"])
                neighbor = (
                    edge["target_node_id"]
                    if edge["source_node_id"] == node_id
                    else edge["source_node_id"]
                )
                if neighbor not in visited and len(visited) < self.config.max_nodes:
                    visited.add(neighbor)
                    queue.append((neighbor, hop + 1))

        hydrated = self.repository.public_chunks_by_ids(chunks, filters=query.filters)
        ordered: list[RetrievalResult] = []
        for chunk_id in chunks:
            row = hydrated.get(chunk_id)
            if row is not None:
                ordered.append(self._result(row, len(ordered) + 1))
        scoped_documents = documents
        if query.filters.document_ids:
            allowed_documents = set(query.filters.document_ids)
            scoped_documents = [item for item in documents if item in allowed_documents]
        scoped_sections = sections
        if query.filters.section_ids:
            allowed_sections = set(query.filters.section_ids)
            scoped_sections = [item for item in sections if item in allowed_sections]
        if scoped_documents and len(ordered) < top_k and (
            not query.filters.section_ids or scoped_sections
        ):
            filters = query.filters.model_copy(
                update={
                    "document_ids": tuple(scoped_documents),
                    "section_ids": tuple(scoped_sections),
                }
            )
            lexical = self.lexical.retrieve(
                query.model_copy(update={"top_k": top_k, "filters": filters})
            )
            known = {item.chunk_id for item in ordered}
            for item in lexical.results:
                if item.chunk_id not in known:
                    ordered.append(
                        item.model_copy(
                            update={
                                "rank": len(ordered) + 1,
                                "retriever": "graph",
                                "graph_rank": len(ordered) + 1,
                            }
                        )
                    )
                    known.add(item.chunk_id)
                if len(ordered) == top_k:
                    break
        return self._response(
            query,
            ordered[:top_k],
            starts,
            len(visited),
            len(edges),
            tuple(documents),
            tuple(sections),
            tuple(chunks),
            started,
            top_k,
        )

    @staticmethod
    def _result(row: sqlite3.Row, rank: int) -> RetrievalResult:
        return RetrievalResult(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            source_id=row["source_id"],
            source_path=row["source_path"],
            scope=row["scope"],
            title=row["title"],
            section_path=json.loads(row["section_path_json"]),
            content=row["content"],
            visibility=row["visibility"],
            revision=row["revision"],
            rank=rank,
            score=1.0 / rank,
            retriever="graph",
            graph_rank=rank,
        )

    @staticmethod
    def _response(
        query: RetrievalQuery,
        results: list[RetrievalResult],
        starts: tuple[str, ...],
        node_count: int,
        edge_count: int,
        documents: tuple[str, ...],
        sections: tuple[str, ...],
        chunks: tuple[str, ...],
        started: float,
        top_k: int,
    ) -> GraphResponse:
        return GraphResponse(
            results=results,
            diagnostics=GraphDiagnostics(
                matched_node_ids=starts,
                graph_nodes_visited=node_count,
                graph_edges_visited=edge_count,
                graph_candidate_documents=len(documents),
                graph_candidate_sections=len(sections),
                provenance_chunk_count=len(chunks),
                returned_count=len(results),
                total_retrieval_latency_ms=(perf_counter() - started) * 1000,
                top_k=top_k,
                filters=query.filters,
            ),
        )
