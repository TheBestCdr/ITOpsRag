"""BM25 keyword ranking, identical to the notebook implementation."""

from __future__ import annotations

from collections import Counter

import numpy as np

from .text import tokenize


class BM25:
    """Standard BM25 ranking over a list of documents."""

    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.tokens = [tokenize(t) for t in texts]
        self.k1 = k1  # term-frequency saturation
        self.b = b  # length-normalization strength
        self.n_docs = len(self.tokens)

        if self.n_docs == 0:
            raise ValueError("BM25 needs at least one document")

        # Average document length, used for length normalization
        self.avg_len = sum(len(t) for t in self.tokens) / self.n_docs

        # Term frequency per document
        self.term_freq = [Counter(t) for t in self.tokens]

        # Document frequency: how many documents contain each term
        doc_freq: Counter[str] = Counter()
        for t in self.tokens:
            doc_freq.update(set(t))

        # Inverse document frequency: rare terms get higher weight
        self.idf = {
            term: float(np.log(1 + (self.n_docs - df + 0.5) / (df + 0.5)))
            for term, df in doc_freq.items()
        }

    def scores(self, query: str) -> np.ndarray:
        """Return one BM25 score per document."""
        query_terms = tokenize(query)
        out = np.zeros(self.n_docs)

        for i, freqs in enumerate(self.term_freq):
            doc_len = len(self.tokens[i])
            score = 0.0
            for term in query_terms:
                if term not in freqs:
                    continue
                f = freqs[term]
                score += self.idf[term] * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * doc_len / self.avg_len)
                )
            out[i] = score

        return out
