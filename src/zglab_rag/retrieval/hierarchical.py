from __future__ import annotations

import sqlite3
from time import perf_counter

from pydantic import BaseModel

from zglab_rag.retrieval.config import HierarchicalRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.lexical import LexicalRetriever
from zglab_rag.retrieval.query import prepare_lexical_query
from zglab_rag.storage.repositories import IndexRepository


class HierarchicalDiagnostics(BaseModel):
    document_candidate_count: int
    section_candidate_count: int
    chunk_candidate_count: int
    returned_count: int
    document_search_ms: float
    section_search_ms: float
    chunk_search_ms: float
    total_retrieval_latency_ms: float
    fallback_used: bool
    selected_document_ids: tuple[str, ...]
    top_k: int
    filters: RetrievalFilter


class HierarchicalResponse(BaseModel):
    results: list[RetrievalResult]
    diagnostics: HierarchicalDiagnostics


class HierarchicalRetriever:
    """FTS-only document → section → real chunk retrieval."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lexical_retriever: LexicalRetriever,
        *,
        config: HierarchicalRetrievalConfig | None = None,
    ) -> None:
        self.repository = IndexRepository(connection)
        self.lexical = lexical_retriever
        self.config = config or HierarchicalRetrievalConfig()

    @property
    def corpus_size(self) -> int:
        return self.lexical.corpus_size

    def retrieve(self, query: RetrievalQuery) -> HierarchicalResponse:
        started = perf_counter()
        top_k = self.config.validate_top_k(query.top_k)
        prepared = prepare_lexical_query(query.text)
        document_started = perf_counter()
        documents = (
            self.repository.search_document_profiles(
                prepared.match_expression or "",
                top_k=self.config.document_candidates,
                source_ids=query.filters.source_ids,
                document_ids=query.filters.document_ids,
            )
            if prepared.applicable
            else []
        )
        document_ms = (perf_counter() - document_started) * 1000
        document_ids = tuple(row["document_id"] for row in documents)
        if not document_ids:
            chunk_started = perf_counter()
            fallback = self.lexical.retrieve(query.model_copy(update={"top_k": top_k}))
            chunk_ms = (perf_counter() - chunk_started) * 1000
            results = [
                item.model_copy(
                    update={
                        "rank": rank,
                        "retriever": "hierarchical",
                        "hierarchical_rank": rank,
                    }
                )
                for rank, item in enumerate(fallback.results, start=1)
            ]
            return self._response(
                query,
                results,
                top_k,
                (),
                0,
                0,
                document_ms,
                0.0,
                chunk_ms,
                started,
                True,
            )

        section_started = perf_counter()
        sections = self.repository.search_sections(
            prepared.match_expression or "",
            document_ids=document_ids,
            top_k=self.config.section_candidates,
        )
        section_ms = (perf_counter() - section_started) * 1000
        section_ids = tuple(row["section_id"] for row in sections)
        if query.filters.section_ids:
            allowed_sections = set(query.filters.section_ids)
            section_ids = tuple(item for item in section_ids if item in allowed_sections)
            if not section_ids:
                return self._response(
                    query,
                    [],
                    top_k,
                    document_ids,
                    len(documents),
                    len(sections),
                    document_ms,
                    section_ms,
                    0.0,
                    started,
                    False,
                )
        filters = query.filters.model_copy(
            update={
                "document_ids": document_ids,
                "section_ids": section_ids,
            }
        )
        chunk_started = perf_counter()
        response = self.lexical.retrieve(
            query.model_copy(
                update={
                    "top_k": max(top_k, self.config.chunk_candidates),
                    "filters": filters,
                }
            )
        )
        chunk_ms = (perf_counter() - chunk_started) * 1000
        results = [
            item.model_copy(
                update={
                    "rank": rank,
                    "retriever": "hierarchical",
                    "hierarchical_rank": rank,
                }
            )
            for rank, item in enumerate(response.results[:top_k], start=1)
        ]
        return self._response(
            query,
            results,
            top_k,
            document_ids,
            len(documents),
            len(sections),
            document_ms,
            section_ms,
            chunk_ms,
            started,
            False,
            chunk_count=len(response.results),
        )

    @staticmethod
    def _response(
        query: RetrievalQuery,
        results: list[RetrievalResult],
        top_k: int,
        document_ids: tuple[str, ...],
        document_count: int,
        section_count: int,
        document_ms: float,
        section_ms: float,
        chunk_ms: float,
        started: float,
        fallback: bool,
        *,
        chunk_count: int | None = None,
    ) -> HierarchicalResponse:
        return HierarchicalResponse(
            results=results,
            diagnostics=HierarchicalDiagnostics(
                document_candidate_count=document_count,
                section_candidate_count=section_count,
                chunk_candidate_count=len(results) if chunk_count is None else chunk_count,
                returned_count=len(results),
                document_search_ms=document_ms,
                section_search_ms=section_ms,
                chunk_search_ms=chunk_ms,
                total_retrieval_latency_ms=(perf_counter() - started) * 1000,
                fallback_used=fallback,
                selected_document_ids=document_ids,
                top_k=top_k,
                filters=query.filters,
            ),
        )
