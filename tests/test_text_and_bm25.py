"""Unit tests for text helpers, BM25, and RRF. No corpus needed."""

from __future__ import annotations

import numpy as np

from itops_rag.bm25 import BM25
from itops_rag.retrieval import reciprocal_rank_fusion, top_indices
from itops_rag.text import quoted_titles, split_sentences, token_f1, tokenize


def test_tokenize_lowercases_and_strips_punctuation():
    assert tokenize("Severity 4: CDN_issue!") == ["severity", "4", "cdn", "issue"]


def test_split_sentences_keeps_abbreviations_simple():
    text = "First point. Second point! Third?"
    assert split_sentences(text) == ["First point.", "Second point!", "Third?"]


def test_quoted_titles_extracts_single_quotes():
    q = "Answer using 'EU cdn_issue incident report (2025-07-13)' please"
    assert quoted_titles(q) == ["EU cdn_issue incident report (2025-07-13)"]


def test_token_f1_identical_strings_is_one():
    assert token_f1("the cdn outage", "the cdn outage") == 1.0


def test_token_f1_disjoint_strings_is_zero():
    assert token_f1("alpha beta", "gamma delta") == 0.0


def test_token_f1_empty_input_is_zero():
    assert token_f1("", "anything") == 0.0


def test_bm25_ranks_matching_document_first():
    docs = [
        "cdn outage in the eu region",
        "billing invoice question",
        "cdn cache rules and latency",
    ]
    bm25 = BM25(docs)
    scores = bm25.scores("cdn outage")
    assert int(np.argmax(scores)) == 0


def test_bm25_requires_documents():
    try:
        BM25([])
    except ValueError:
        return
    raise AssertionError("BM25 should reject an empty document list")


def test_top_indices_orders_by_score():
    scores = np.array([0.1, 0.9, 0.5])
    assert top_indices(scores, 2) == [1, 2]


def test_rrf_rewards_agreement_between_rankings():
    # Document 1 is first in both rankings, so it must win.
    fused = reciprocal_rank_fusion([[1, 0], [1, 2]], n_docs=3, k=60)
    assert int(np.argmax(fused)) == 1
