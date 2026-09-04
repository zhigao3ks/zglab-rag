from __future__ import annotations

import json
import sqlite3
from time import perf_counter

from pydantic import BaseModel

from zglab_rag.domain.lexical import DEFAULT_LEXICAL_PROFILE, LexicalProfile
from zglab_rag.retrieval.config import VectorRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.errors import LexicalProfileMismatch
from zglab_rag.retrieval.query import prepare_lexical_query
from zglab_rag.storage.repositories import IndexRepository


class LexicalDiagnostics(BaseModel):
    lexical_search_latency_ms: float
    total_retrieval_latency_ms: float
    candidate_count: int
    returned_count: int
    top_k: int
    filters: RetrievalFilter
    lexical_applicable: bool
    lexical_not_applicable_reason: str | None = None


class LexicalResponse(BaseModel):
    results: list[RetrievalResult]
    diagnostics: LexicalDiagnostics


class LexicalRetriever:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        profile: LexicalProfile = DEFAULT_LEXICAL_PROFILE,
        config: VectorRetrievalConfig | None = None,
    ) -> None:
        self.repository = IndexRepository(connection)
        self.profile = profile
        self.config = config or VectorRetrievalConfig()
        self._validate_profile()

    @property
    def corpus_size(self) -> int:
        return self.repository.lexical_count()

    def retrieve(self, query: RetrievalQuery) -> LexicalResponse:
        started = perf_counter()
        top_k = self.config.validate_top_k(query.top_k)
        self._validate_profile()
        prepared = prepare_lexical_query(query.text)
        if not prepared.applicable:
            return LexicalResponse(
                results=[],
                diagnostics=LexicalDiagnostics(
                    lexical_search_latency_ms=0.0,
                    total_retrieval_latency_ms=(perf_counter() - started) * 1000,
                    candidate_count=0,
                    returned_count=0,
                    top_k=top_k,
                    filters=query.filters,
                    lexical_applicable=False,
                    lexical_not_applicable_reason=prepared.reason,
                ),
            )
        search_started = perf_counter()
        rows = self.repository.lexical_search(
            prepared.match_expression or "",
            top_k=top_k,
            visibility=query.filters.visibility.value,
            title_weight=self.profile.title_weight,
            section_weight=self.profile.section_weight,
            content_weight=self.profile.content_weight,
            source_ids=query.filters.source_ids,
            scopes=[scope.value for scope in query.filters.scopes],
            document_ids=query.filters.document_ids,
            section_ids=query.filters.section_ids,
        )
        search_ms = (perf_counter() - search_started) * 1000
        results = [
            RetrievalResult(
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
                score=-float(row["raw_bm25"]),
                raw_bm25=float(row["raw_bm25"]),
                retriever="lexical",
            )
            for rank, row in enumerate(rows, start=1)
        ]
        return LexicalResponse(
            results=results,
            diagnostics=LexicalDiagnostics(
                lexical_search_latency_ms=search_ms,
                total_retrieval_latency_ms=(perf_counter() - started) * 1000,
                candidate_count=len(rows),
                returned_count=len(results),
                top_k=top_k,
                filters=query.filters,
                lexical_applicable=True,
            ),
        )

    def _validate_profile(self) -> None:
        active = self.repository.active_lexical_profile_id()
        if active != self.profile.profile_id:
            raise LexicalProfileMismatch(
                f"Lexical profile does not match active index: database={active}, "
                f"requested={self.profile.profile_id}"
            )
        row = self.repository.lexical_profile(active)
        if row is None or row["config_hash"] != self.profile.config_hash:
            raise LexicalProfileMismatch("Active lexical profile metadata is missing or mismatched")
