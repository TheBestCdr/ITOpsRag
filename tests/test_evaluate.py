"""Tests for the evaluation harness."""

from __future__ import annotations

from itops_rag.evaluate import evaluate_answers, evaluate_retrieval

from .conftest import requires_data


@requires_data
def test_retrieval_metrics_are_in_range(service, corpus):
    from itops_rag.corpus import build_eval_set

    eval_set = build_eval_set(corpus).head(25)
    for method in ("bm25", "vector", "hybrid"):
        result = evaluate_retrieval(service, eval_set, method)
        assert 0.0 <= result["recall_at_k"] <= 1.0
        assert 0.0 <= result["mrr"] <= 1.0
        assert result["n_questions"] == 25


@requires_data
def test_answer_metrics_are_in_range(service, corpus):
    from itops_rag.corpus import build_eval_set

    eval_set = build_eval_set(corpus).head(15)
    result = evaluate_answers(service, eval_set, "hybrid")

    assert 0.0 <= result["token_f1"] <= 1.0
    assert 0.0 <= result["citation_precision_doc"] <= 1.0
    assert 0.0 <= result["abstention_rate"] <= 1.0
    assert result["latency_ms_mean"] > 0
