"""Retrieval interfaces and the local (notebook-parity) implementation.

Two backends implement the same ``Retriever`` protocol:

* ``LocalRetriever``  - in-process BM25 + sentence-transformer embeddings + RRF.
  This is the notebook algorithm and is the evaluation baseline.
* ``AzureSearchRetriever`` - Azure AI Search BM25 / vector / hybrid.

Keeping both lets us compare the deployed system against the notebook numbers
instead of silently replacing them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from .bm25 import BM25
from .config import Settings
from .text import quoted_titles


@dataclass(frozen=True)
class RetrievedChunk:
    """One retrieval hit."""

    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    text: str
    score: float
    rank: int


class Retriever(Protocol):
    """Common interface for every retrieval backend."""

    def search(
        self,
        question: str,
        method: str = "hybrid",
        k: int = 5,
        use_title_anchor: bool = True,
    ) -> list[RetrievedChunk]:
        ...

    def best_similarity(self, question: str) -> float:
        """Highest cosine similarity between the question and any chunk."""
        ...


def top_indices(scores: np.ndarray, n: int) -> list[int]:
    """Indices of the n highest scores, best first."""
    return [int(i) for i in np.argsort(-scores)[:n]]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]], n_docs: int, k: int = 60
) -> np.ndarray:
    """Merge several ranked lists into one score vector."""
    fused = np.zeros(n_docs)
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] += 1.0 / (k + rank + 1)
    return fused


class LocalRetriever:
    """Notebook-parity retriever: BM25 + embeddings + RRF + title anchoring."""

    def __init__(
        self,
        chunks: pd.DataFrame,
        settings: Settings,
        embedder: object | None = None,
    ) -> None:
        self.chunks = chunks.reset_index(drop=True)
        self.settings = settings

        self.chunk_ids = self.chunks["chunk_id"].to_numpy()
        self.doc_ids = self.chunks["doc_id"].to_numpy()
        self.titles = self.chunks["title"].to_numpy()
        self.doc_types = self.chunks["doc_type"].to_numpy()
        self.texts = self.chunks["chunk_text"].to_numpy()

        # Index the title together with the chunk text: titles carry strong signal.
        self.bm25 = BM25((self.chunks["title"] + " " + self.chunks["chunk_text"]).tolist())

        self._embedder = embedder
        self._embeddings: np.ndarray | None = None

    # ---- embeddings -------------------------------------------------

    @property
    def embedder(self):
        """Lazily load the sentence-transformer so import stays cheap."""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer(self.settings.local_embedding_model)
        return self._embedder

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        return np.asarray(
            self.embedder.encode(
                texts,
                normalize_embeddings=True,
                batch_size=64,
                show_progress_bar=False,
            )
        )

    def embed_question(self, question: str) -> np.ndarray:
        return np.asarray(
            self.embedder.encode(
                [question], normalize_embeddings=True, show_progress_bar=False
            )[0]
        )

    @property
    def embeddings(self) -> np.ndarray:
        """Chunk embedding matrix, computed once and cached."""
        if self._embeddings is None:
            self._embeddings = self.embed_texts(
                (self.chunks["title"] + ". " + self.chunks["chunk_text"]).tolist()
            )
        return self._embeddings

    # ---- title anchoring --------------------------------------------

    def title_anchors(self, question: str) -> list[int]:
        """Chunk indices whose document title is quoted (or mentioned) in the question."""
        q_lower = question.lower()

        anchors: list[int] = []
        for quoted in quoted_titles(question):
            quoted_lower = quoted.lower()
            anchors += [i for i, t in enumerate(self.titles) if t.lower() == quoted_lower]

        if not anchors:
            anchors = [i for i, t in enumerate(self.titles) if t.lower() in q_lower]

        return anchors

    # ---- search -----------------------------------------------------

    def _scores(self, question: str, method: str) -> np.ndarray:
        keyword_scores = self.bm25.scores(question)
        vector_scores = self.embeddings @ self.embed_question(question)

        if method == "bm25":
            return keyword_scores
        if method == "vector":
            return vector_scores
        if method == "hybrid":
            return reciprocal_rank_fusion(
                [
                    top_indices(keyword_scores, self.settings.candidate_pool),
                    top_indices(vector_scores, self.settings.candidate_pool),
                ],
                n_docs=len(self.chunks),
                k=self.settings.rrf_k,
            )
        raise ValueError(f"unknown method: {method}")

    def search(
        self,
        question: str,
        method: str = "hybrid",
        k: int = 5,
        use_title_anchor: bool = True,
    ) -> list[RetrievedChunk]:
        scores = self._scores(question, method)

        if use_title_anchor:
            anchors = self.title_anchors(question)
            if anchors:
                anchors = sorted(set(anchors), key=lambda j: -scores[j])
                anchor_set = set(anchors)
                rest = [
                    j
                    for j in top_indices(scores, self.settings.candidate_pool)
                    if j not in anchor_set
                ]
                indices = (anchors + rest)[:k]
                return self._to_chunks(indices, scores)

        return self._to_chunks(top_indices(scores, k), scores)

    def _to_chunks(self, indices: Sequence[int], scores: np.ndarray) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id=str(self.chunk_ids[j]),
                doc_id=str(self.doc_ids[j]),
                title=str(self.titles[j]),
                doc_type=str(self.doc_types[j]),
                text=str(self.texts[j]),
                score=float(scores[j]),
                rank=rank,
            )
            for rank, j in enumerate(indices)
        ]

    def best_similarity(self, question: str) -> float:
        return float((self.embeddings @ self.embed_question(question)).max())
