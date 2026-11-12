"""Tests for the Azure AI Search retriever using a fake search client."""

from __future__ import annotations

import numpy as np

from itops_rag.azure_search import AzureSearchRetriever, corpus_fingerprint
from itops_rag.config import Settings


class FakeEmbedder:
    """Deterministic 4-dim embedder so tests need no model download."""

    def embed_texts(self, texts):
        return np.array([[1.0, 0.0, 0.0, 0.0] for _ in texts], dtype=np.float32)

    def embed_question(self, question):
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


class FakeSearchClient:
    def __init__(self, hits):
        self.hits = hits
        self.calls: list[dict] = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        # Vector-only queries return the vector ordering; keyword returns keyword order.
        if kwargs.get("search_text") is None:
            return list(reversed(self.hits))
        return list(self.hits)


def _hit(chunk_id: str, doc_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "title": "T",
        "doc_type": "incident_report",
        "chunk_text": "text",
        "@search.score": 1.0,
    }


def _settings() -> Settings:
    return Settings(
        retriever_backend="azure",
        azure_search_endpoint="https://example.search.windows.net",
        candidate_pool=10,
    )


def test_bm25_uses_keyword_search():
    client = FakeSearchClient([_hit("doc_0001_chunk_1", "doc_0001")])
    retriever = AzureSearchRetriever(_settings(), FakeEmbedder(), client=client)

    results = retriever.search("cdn outage", method="bm25", k=1)

    assert results[0].chunk_id == "doc_0001_chunk_1"
    assert client.calls[0]["search_text"] == "cdn outage"


def test_vector_uses_vector_query_only():
    client = FakeSearchClient([_hit("doc_0001_chunk_1", "doc_0001")])
    retriever = AzureSearchRetriever(_settings(), FakeEmbedder(), client=client)

    retriever.search("cdn outage", method="vector", k=1)

    assert client.calls[0]["search_text"] is None
    assert client.calls[0]["vector_queries"]


def test_hybrid_fuses_both_rankings():
    hits = [
        _hit("doc_0001_chunk_1", "doc_0001"),
        _hit("doc_0002_chunk_1", "doc_0002"),
    ]
    client = FakeSearchClient(hits)
    retriever = AzureSearchRetriever(_settings(), FakeEmbedder(), client=client)

    results = retriever.search("cdn outage", method="hybrid", k=2)

    # Both documents appear exactly once in the fused output.
    assert {r.chunk_id for r in results} == {"doc_0001_chunk_1", "doc_0002_chunk_1"}
    assert len(client.calls) == 2


def test_unknown_method_is_rejected():
    retriever = AzureSearchRetriever(_settings(), FakeEmbedder(), client=FakeSearchClient([]))
    try:
        retriever.search("q", method="magic")
    except ValueError:
        return
    raise AssertionError("unknown method should raise")


def test_best_similarity_is_cosine_of_top_hit():
    client = FakeSearchClient([_hit("doc_0001_chunk_1", "doc_0001")])
    retriever = AzureSearchRetriever(_settings(), FakeEmbedder(), client=client)

    # Identical unit vectors -> cosine 1.0
    assert retriever.best_similarity("q") == 1.0


def test_best_similarity_is_zero_without_hits():
    retriever = AzureSearchRetriever(_settings(), FakeEmbedder(), client=FakeSearchClient([]))
    assert retriever.best_similarity("q") == 0.0


def test_corpus_fingerprint_is_stable_and_order_sensitive():
    import pandas as pd

    a = pd.DataFrame({"chunk_id": ["doc_0001_chunk_1", "doc_0002_chunk_1"]})
    b = pd.DataFrame({"chunk_id": ["doc_0001_chunk_1", "doc_0002_chunk_1"]})
    c = pd.DataFrame({"chunk_id": ["doc_0002_chunk_1", "doc_0001_chunk_1"]})

    assert corpus_fingerprint(a) == corpus_fingerprint(b)
    assert corpus_fingerprint(a) != corpus_fingerprint(c)
