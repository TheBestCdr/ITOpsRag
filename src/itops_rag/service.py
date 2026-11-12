"""Service layer: one object that owns retrieval + generation.

The API, the evaluation harness, and the tests all call this, so behaviour is
identical everywhere.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .config import Settings
from .corpus import Corpus
from .generation import Answer, ExtractiveGenerator, Generator
from .retrieval import LocalRetriever, Retriever

logger = logging.getLogger(__name__)


@dataclass
class AskResult:
    """Everything the API returns for one question."""

    question: str
    answer: str
    citations: list[str]
    grounded: bool
    method: str
    evidence: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)


def build_retriever(settings: Settings, corpus: Corpus | None) -> Retriever:
    """Construct the configured retrieval backend."""
    backend = settings.retriever_backend.lower()

    if backend == "local":
        if corpus is None:
            raise ValueError("the local retriever needs the corpus loaded")
        return LocalRetriever(corpus.chunks, settings)

    if backend == "azure":
        from .azure_openai import AzureOpenAIEmbedder
        from .azure_search import AzureSearchRetriever

        embedder = AzureOpenAIEmbedder(settings)
        return AzureSearchRetriever(settings, embedder)

    raise ValueError(f"unknown RETRIEVER_BACKEND: {settings.retriever_backend}")


def build_generator(settings: Settings) -> Generator:
    """Construct the configured answer generator."""
    backend = settings.generator_backend.lower()

    if backend == "extractive":
        return ExtractiveGenerator(settings)

    if backend == "azure_openai":
        from .azure_openai import AzureOpenAIGenerator

        return AzureOpenAIGenerator(settings)

    raise ValueError(f"unknown GENERATOR_BACKEND: {settings.generator_backend}")


class RagService:
    """Retrieval-augmented answering over the IT operations corpus."""

    def __init__(
        self,
        settings: Settings,
        corpus: Corpus | None,
        retriever: Retriever | None = None,
        generator: Generator | None = None,
    ) -> None:
        self.settings = settings
        self.corpus = corpus
        self.retriever = retriever or build_retriever(settings, corpus)
        self.generator = generator or build_generator(settings)

    # ---- public API -------------------------------------------------

    def search(
        self,
        question: str,
        method: str = "hybrid",
        k: int | None = None,
        use_title_anchor: bool | None = None,
    ) -> list[dict]:
        """Return retrieval hits for inspection (no answer generation)."""
        top_k = k or self.settings.top_k
        anchor = (
            self.settings.use_title_anchor if use_title_anchor is None else use_title_anchor
        )

        hits = self.retriever.search(
            question, method=method, k=top_k, use_title_anchor=anchor
        )
        return [
            {
                "chunk_id": h.chunk_id,
                "doc_id": h.doc_id,
                "title": h.title,
                "doc_type": h.doc_type,
                "text": h.text,
                "score": h.score,
                "rank": h.rank,
            }
            for h in hits
        ]

    def ask(
        self,
        question: str,
        method: str = "hybrid",
        k: int | None = None,
        use_title_anchor: bool | None = None,
    ) -> AskResult:
        """Retrieve evidence and produce a grounded answer with citations."""
        start = time.perf_counter()

        top_k = k or self.settings.top_k
        anchor = (
            self.settings.use_title_anchor if use_title_anchor is None else use_title_anchor
        )

        hits = self.retriever.search(
            question, method=method, k=top_k, use_title_anchor=anchor
        )
        best_similarity = self.retriever.best_similarity(question)

        answer: Answer = self.generator.generate(
            question, hits, best_similarity, method=method
        )

        latency_ms = (time.perf_counter() - start) * 1000

        return AskResult(
            question=question,
            answer=answer.answer,
            citations=answer.citations,
            grounded=answer.grounded,
            method=method,
            evidence=[
                {
                    "chunk_id": h.chunk_id,
                    "doc_id": h.doc_id,
                    "title": h.title,
                    "doc_type": h.doc_type,
                    "text": h.text,
                    "score": h.score,
                }
                for h in hits
            ],
            latency_ms=latency_ms,
            usage=answer.usage,
        )

    # ---- readiness --------------------------------------------------

    def health(self) -> dict:
        """Report corpus size and configured backends.

        When the corpus is not mounted (the Azure deployment reads chunks from
        Azure AI Search instead), report the expected corpus size and the live
        index document count.
        """
        if self.corpus is not None:
            documents = int(self.corpus.chunks["doc_id"].nunique())
            chunks = int(len(self.corpus.chunks))
        else:
            documents = self.settings.expected_docs
            chunks = self.settings.expected_chunks

        info = {
            "status": "ok",
            "documents": documents,
            "chunks": chunks,
            "retriever_backend": self.settings.retriever_backend,
            "generator_backend": self.settings.generator_backend,
            "corpus_loaded": self.corpus is not None,
        }

        # For the Azure backend, report what is actually in the index.
        if self.settings.retriever_backend.lower() == "azure":
            try:
                from .azure_search import index_document_count

                info["index_documents"] = index_document_count(self.settings)
            except Exception as exc:  # pragma: no cover - depends on Azure
                info["index_documents"] = None
                info["index_error"] = str(exc)[:200]

        return info
