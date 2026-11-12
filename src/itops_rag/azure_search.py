"""Azure AI Search backend: index schema, idempotent indexing, and retrieval.

The index stores the original ``chunk_id`` / ``doc_id`` so citations stay
comparable with the notebook. Indexing is idempotent: documents are keyed by
``chunk_id`` and uploaded with ``merge_or_upload``, so re-running is safe.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence

import numpy as np
import pandas as pd

from .config import Settings
from .retrieval import RetrievedChunk, reciprocal_rank_fusion, top_indices

logger = logging.getLogger(__name__)


def _search_client(settings: Settings):
    from azure.core.credentials import AzureKeyCredential
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient

    if not settings.azure_search_endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is not configured")

    credential: AzureKeyCredential | DefaultAzureCredential
    if settings.azure_search_api_key:
        credential = AzureKeyCredential(settings.azure_search_api_key)
    else:
        credential = DefaultAzureCredential()

    return SearchClient(
        endpoint=settings.azure_search_endpoint,
        index_name=settings.azure_search_index,
        credential=credential,
    )


def _index_client(settings: Settings):
    from azure.core.credentials import AzureKeyCredential
    from azure.identity import DefaultAzureCredential
    from azure.search.documents.indexes import SearchIndexClient

    if not settings.azure_search_endpoint:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is not configured")

    credential: AzureKeyCredential | DefaultAzureCredential
    if settings.azure_search_api_key:
        credential = AzureKeyCredential(settings.azure_search_api_key)
    else:
        credential = DefaultAzureCredential()

    return SearchIndexClient(endpoint=settings.azure_search_endpoint, credential=credential)


def build_index(settings: Settings, dimensions: int | None = None) -> None:
    """Create or update the search index schema.

    Only the fields we query are indexed, which keeps the index small enough for
    the Free tier (50 MB).
    """
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    dims = dimensions or settings.azure_embedding_dimensions

    fields = [
        SimpleField(name="chunk_id", type=SearchFieldDataType.String, key=True, filterable=True),
        SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SimpleField(name="doc_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchableField(name="chunk_text", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dims,
            vector_search_profile_name="default-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="default-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="default-profile",
                algorithm_configuration_name="default-hnsw",
            )
        ],
    )

    index = SearchIndex(
        name=settings.azure_search_index,
        fields=fields,
        vector_search=vector_search,
    )

    client = _index_client(settings)
    client.create_or_update_index(index)
    logger.info("index %s ready with %d dimensions", settings.azure_search_index, dims)


def index_corpus(
    settings: Settings,
    chunks: pd.DataFrame,
    embedder,
    *,
    batch_size: int = 200,
) -> int:
    """Embed and upload every chunk. Idempotent: safe to re-run.

    Returns the number of documents uploaded.
    """
    client = _search_client(settings)

    texts = (chunks["title"] + ". " + chunks["chunk_text"]).tolist()
    vectors = embedder.embed_texts(texts)

    uploaded = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks.iloc[start : start + batch_size]
        batch_vectors = vectors[start : start + batch_size]

        documents = [
            {
                "chunk_id": str(row.chunk_id),
                "doc_id": str(row.doc_id),
                "title": str(row.title),
                "doc_type": str(row.doc_type),
                "chunk_text": str(row.chunk_text),
                "content_vector": batch_vectors[i].tolist(),
            }
            for i, row in enumerate(batch.itertuples(index=False))
        ]

        client.merge_or_upload_documents(documents=documents)
        uploaded += len(documents)
        logger.info("indexed %d/%d chunks", uploaded, len(chunks))

    return uploaded


def corpus_fingerprint(chunks: pd.DataFrame) -> str:
    """Stable hash of the corpus, used to skip redundant re-indexing."""
    digest = hashlib.sha256()
    for chunk_id in chunks["chunk_id"]:
        digest.update(str(chunk_id).encode())
    return digest.hexdigest()[:16]


class AzureSearchRetriever:
    """Retrieval against Azure AI Search (BM25, vector, and hybrid)."""

    def __init__(self, settings: Settings, embedder, client=None) -> None:
        self.settings = settings
        self.embedder = embedder
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = _search_client(self.settings)
        return self._client

    def _to_chunks(self, results: Sequence[dict]) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id=str(r["chunk_id"]),
                doc_id=str(r["doc_id"]),
                title=str(r["title"]),
                doc_type=str(r["doc_type"]),
                text=str(r["chunk_text"]),
                score=float(r.get("@search.score", 0.0)),
                rank=rank,
            )
            for rank, r in enumerate(results)
        ]

    def _keyword(self, question: str, k: int) -> list[dict]:
        return list(self.client.search(search_text=question, top=k))

    def _vector(self, question: str, k: int) -> list[dict]:
        from azure.search.documents.models import VectorizedQuery

        vector = self.embedder.embed_question(question)
        return list(
            self.client.search(
                search_text=None,
                vector_queries=[
                    VectorizedQuery(
                        vector=vector.tolist(),
                        k_nearest_neighbors=k,
                        fields="content_vector",
                    )
                ],
                top=k,
            )
        )

    def search(
        self,
        question: str,
        method: str = "hybrid",
        k: int = 5,
        use_title_anchor: bool = True,
    ) -> list[RetrievedChunk]:
        pool = max(k, self.settings.candidate_pool)

        if method == "bm25":
            return self._to_chunks(self._keyword(question, k))
        if method == "vector":
            return self._to_chunks(self._vector(question, k))
        if method != "hybrid":
            raise ValueError(f"unknown method: {method}")

        # Hybrid: fuse the two Azure rankings with the same RRF used locally.
        keyword_hits = self._keyword(question, pool)
        vector_hits = self._vector(question, pool)

        by_id: dict[str, dict] = {}
        for hit in keyword_hits + vector_hits:
            by_id.setdefault(str(hit["chunk_id"]), hit)

        ids = list(by_id)
        index_of = {chunk_id: i for i, chunk_id in enumerate(ids)}

        keyword_ranking = [index_of[str(h["chunk_id"])] for h in keyword_hits]
        vector_ranking = [index_of[str(h["chunk_id"])] for h in vector_hits]

        fused = reciprocal_rank_fusion(
            [keyword_ranking, vector_ranking], n_docs=len(ids), k=self.settings.rrf_k
        )
        order = top_indices(fused, k)

        results = []
        for i in order:
            hit = dict(by_id[ids[i]])
            hit["@search.score"] = float(fused[i])
            results.append(hit)

        return self._to_chunks(results)

    def best_similarity(self, question: str) -> float:
        """Approximate the local cosine gate using the top vector hit.

        Azure returns a relevance score, not a cosine value, so we recompute the
        cosine against the returned chunk text to keep the abstention rule
        identical across backends.
        """
        hits = self._vector(question, 1)
        if not hits:
            return 0.0

        question_vector = self.embedder.embed_question(question)
        chunk_vector = self.embedder.embed_texts([hits[0]["chunk_text"]])[0]
        return float(np.dot(question_vector, chunk_vector))
