from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.test_lexical_hybrid import FakeEmbeddingProvider, _profile, _source_input
from zglab_rag.indexing.indexer import KnowledgeIndexer
from zglab_rag.retrieval.config import (
    GraphRetrievalConfig,
    HierarchicalRetrievalConfig,
    IntelligentRetrievalConfig,
    VectorRetrievalConfig,
)
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.graph import GraphRetriever
from zglab_rag.retrieval.hierarchical import HierarchicalRetriever
from zglab_rag.retrieval.hybrid import HybridRetriever
from zglab_rag.retrieval.intelligent import IntelligentRetriever, intelligent_rrf
from zglab_rag.retrieval.lexical import LexicalRetriever
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database


@pytest.fixture
def phase16_retrievers(tmp_path):
    connection = Database(tmp_path / "retrieval.db").connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [
            _source_input(
                "notes",
                [
                    ("mcp", "MCP Model Context Protocol tool authorization"),
                    ("memory", "Agent memory context hierarchy"),
                ],
            ),
            _source_input("other", [("sqlite", "SQLite transaction rollback")]),
        ]
    )
    lexical = LexicalRetriever(
        connection, config=VectorRetrievalConfig(default_top_k=5)
    )
    return (
        connection,
        HierarchicalRetriever(connection, lexical),
        GraphRetriever(connection, lexical),
    )


def test_hierarchical_document_section_and_real_chunk_flow(phase16_retrievers) -> None:
    _, hierarchical, _ = phase16_retrievers
    response = hierarchical.retrieve(RetrievalQuery(text="MCP protocol", top_k=5))
    assert response.results[0].chunk_id == "mcp"
    assert response.results[0].retriever == "hierarchical"
    assert response.diagnostics.document_candidate_count >= 1
    assert response.diagnostics.section_candidate_count >= 1
    assert not response.diagnostics.fallback_used


def test_hierarchical_empty_candidates_use_flat_lexical_fallback(
    phase16_retrievers,
) -> None:
    _, hierarchical, _ = phase16_retrievers
    response = hierarchical.retrieve(RetrievalQuery(text="不存在词语xyz", top_k=3))
    assert response.diagnostics.fallback_used
    assert response.results == []


def test_hierarchical_document_and_section_filters_are_server_derived(
    phase16_retrievers,
) -> None:
    _, hierarchical, _ = phase16_retrievers
    response = hierarchical.retrieve(
        RetrievalQuery(
            text="SQLite transaction",
            filters=RetrievalFilter(
                document_ids=("notes:knowledge/notes.md",)
            ),
        )
    )
    assert all(item.document_id == "notes:knowledge/notes.md" for item in response.results)

    blocked = hierarchical.retrieve(
        RetrievalQuery(
            text="MCP protocol",
            filters=RetrievalFilter(section_ids=("section-does-not-exist",)),
        )
    )
    assert blocked.results == []


def test_graph_nfkc_alias_resolves_to_public_provenance_chunk(phase16_retrievers) -> None:
    _, _, graph = phase16_retrievers
    response = graph.retrieve(RetrievalQuery(text="ＭＣＰ 是什么？", top_k=5))
    assert "technology:mcp" in response.diagnostics.matched_node_ids
    assert response.results[0].chunk_id == "mcp"
    assert response.results[0].retriever == "graph"
    assert response.diagnostics.provenance_chunk_count >= 1


def test_graph_no_entity_is_empty_not_flat_fallback(phase16_retrievers) -> None:
    _, _, graph = phase16_retrievers
    response = graph.retrieve(RetrievalQuery(text="completely unrelated xyz"))
    assert response.results == []
    assert response.diagnostics.matched_node_ids == ()


def test_graph_longest_alias_wins_over_overlapping_shorter_alias(
    phase16_retrievers,
) -> None:
    connection, _, graph = phase16_retrievers
    connection.execute(
        "INSERT INTO graph_nodes VALUES('topic:context','TOPIC','Context','context','{}')"
    )
    connection.execute(
        "INSERT INTO graph_aliases VALUES('context protocol','topic:context')"
    )
    assert graph.resolve_entities("Model Context Protocol") == ("technology:mcp",)


def test_graph_contains_edges_are_structural(phase16_retrievers) -> None:
    connection, _, _ = phase16_retrievers
    row = connection.execute(
        "SELECT provenance_kind FROM graph_edges WHERE relation='CONTAINS' LIMIT 1"
    ).fetchone()
    assert row[0] == "STRUCTURAL"


def test_graph_traversal_obeys_node_edge_and_hop_bounds(phase16_retrievers) -> None:
    connection, _, _ = phase16_retrievers
    lexical = LexicalRetriever(connection)
    graph = GraphRetriever(
        connection,
        lexical,
        config=GraphRetrievalConfig(max_hops=1, max_nodes=2, max_edges=1),
    )
    response = graph.retrieve(RetrievalQuery(text="MCP"))
    assert response.diagnostics.graph_nodes_visited <= 2
    assert response.diagnostics.graph_edges_visited <= 1


def _result(chunk_id: str, rank: int = 1) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id="doc",
        source_id="source",
        source_path="source.md",
        scope="knowledge",
        title="title",
        section_path=["section"],
        content="real chunk",
        visibility="public",
        revision="r",
        rank=rank,
        score=1.0,
    )


def test_intelligent_rrf_deduplicates_and_preserves_component_ranks() -> None:
    fused = intelligent_rrf(
        [_result("same"), _result("hybrid", 2)],
        [_result("same"), _result("hierarchy", 2)],
        [_result("same")],
        config=IntelligentRetrievalConfig(),
    )
    assert [item.chunk_id for item in fused].count("same") == 1
    assert fused[0].hybrid_rank == fused[0].hierarchical_rank == fused[0].graph_rank == 1
    assert fused[0].fusion_score


def test_intelligent_rrf_tie_break_is_deterministic() -> None:
    config = IntelligentRetrievalConfig()
    first = intelligent_rrf([_result("b")], [_result("a")], [], config=config)
    second = intelligent_rrf([_result("b")], [_result("a")], [], config=config)
    assert [item.chunk_id for item in first] == ["a", "b"]
    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]


class _FakeRoute:
    corpus_size = 10

    def __init__(self, results):
        self.results = results
        self.calls = 0

    def retrieve(self, _query):
        self.calls += 1
        return SimpleNamespace(results=self.results)


@pytest.mark.parametrize(
    ("hierarchy", "graph"),
    [([], [_result("g")]), ([_result("h")], []), ([], [])],
)
def test_intelligent_component_empty_paths_remain_available(hierarchy, graph) -> None:
    hybrid = _FakeRoute([_result("hybrid")])
    retriever = IntelligentRetriever(
        hybrid, _FakeRoute(hierarchy), _FakeRoute(graph)
    )
    response = retriever.retrieve(RetrievalQuery(text="query", top_k=2))
    assert response.results
    assert any(item.chunk_id == "hybrid" for item in response.results)
    assert hybrid.calls == 1
    assert len(response.results) <= 2


def test_hierarchical_config_bounds() -> None:
    with pytest.raises(ValueError):
        HierarchicalRetrievalConfig(document_candidates=0)


def test_intelligent_performs_exactly_one_query_embedding(tmp_path) -> None:
    class CountingProvider(FakeEmbeddingProvider):
        query_calls = 0

        def encode_queries(self, texts):
            self.query_calls += 1
            return super().encode_queries(texts)

    connection = Database(tmp_path / "counting.db").connect()
    provider = CountingProvider()
    KnowledgeIndexer(connection, provider, _profile()).build(
        [_source_input("notes", [("mcp", "MCP Model Context Protocol")])]
    )
    vector = VectorRetriever(connection, provider, _profile())
    lexical = LexicalRetriever(connection)
    intelligent = IntelligentRetriever(
        HybridRetriever(vector, lexical),
        HierarchicalRetriever(connection, lexical),
        GraphRetriever(connection, lexical),
    )
    intelligent.retrieve(RetrievalQuery(text="MCP", top_k=1))
    assert provider.query_calls == 1
