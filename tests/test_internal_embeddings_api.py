from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from tests.test_public_api import FakeRuntime
from zglab_rag.api.main import create_app
from zglab_rag.config import Settings


class RecordingEmbeddingProvider:
    model_name = "BAAI/bge-small-zh-v1.5"
    dimension = 3
    config = SimpleNamespace(normalize=True)

    def __init__(self) -> None:
        self.query_calls: list[list[str]] = []
        self.document_calls: list[list[str]] = []

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        self.query_calls.append(texts)
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    def encode_documents(self, texts: list[str]) -> np.ndarray:
        self.document_calls.append(texts)
        return np.asarray([[0.0, 1.0, 0.0] for _ in texts], dtype=np.float32)


def _client() -> tuple[TestClient, FakeRuntime, RecordingEmbeddingProvider]:
    settings = Settings(
        internal_embedding_token="test-internal-token",
        internal_embedding_max_texts=2,
        internal_embedding_max_text_chars=8,
    )
    runtime = FakeRuntime(settings=settings)
    provider = RecordingEmbeddingProvider()
    runtime.embedding_components = SimpleNamespace(provider=provider)
    return TestClient(create_app(runtime=runtime, settings=settings)), runtime, provider


def test_query_and_document_routes_preserve_provider_semantics() -> None:
    client, _, provider = _client()

    query = client.post(
        "/internal/embeddings/query",
        headers={"X-Internal-Token": "test-internal-token"},
        json={"texts": ["问题"]},
    )
    documents = client.post(
        "/internal/embeddings/documents",
        headers={"X-Internal-Token": "test-internal-token"},
        json={"texts": ["资料一", "资料二"]},
    )

    assert query.status_code == 200
    assert query.json() == {
        "model": "BAAI/bge-small-zh-v1.5",
        "dimension": 3,
        "normalized": True,
        "embeddings": [[1.0, 0.0, 0.0]],
    }
    assert documents.status_code == 200
    assert documents.json()["embeddings"] == [[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]]
    assert provider.query_calls == [["问题"]]
    assert provider.document_calls == [["资料一", "资料二"]]


def test_invalid_or_unconfigured_token_is_hidden() -> None:
    client, _, provider = _client()

    missing = client.post("/internal/embeddings/query", json={"texts": ["问题"]})
    invalid = client.post(
        "/internal/embeddings/query",
        headers={"X-Internal-Token": "wrong"},
        json={"texts": ["问题"]},
    )

    assert missing.status_code == 404
    assert invalid.status_code == 404
    assert provider.query_calls == []


def test_limits_reject_before_provider_call() -> None:
    client, _, provider = _client()
    headers = {"X-Internal-Token": "test-internal-token"}

    too_many = client.post(
        "/internal/embeddings/documents",
        headers=headers,
        json={"texts": ["一", "二", "三"]},
    )
    too_long = client.post(
        "/internal/embeddings/documents",
        headers=headers,
        json={"texts": ["超过八个字符的文本内容"]},
    )
    blank = client.post(
        "/internal/embeddings/documents", headers=headers, json={"texts": ["  "]}
    )

    assert too_many.status_code == 413
    assert too_long.status_code == 422
    assert blank.status_code == 422
    assert provider.document_calls == []


def test_endpoint_reuses_injected_runtime_provider() -> None:
    client, runtime, provider = _client()
    provider_identity = id(runtime.embedding_components.provider)

    response = client.post(
        "/internal/embeddings/query",
        headers={"X-Internal-Token": "test-internal-token"},
        json={"texts": ["问题"]},
    )

    assert response.status_code == 200
    assert id(runtime.embedding_components.provider) == provider_identity
    assert runtime.embedding_components.provider is provider
