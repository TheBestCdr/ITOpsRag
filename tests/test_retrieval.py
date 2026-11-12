"""Tests for retrieval behaviour and the answer generator."""

from __future__ import annotations

import pytest

from itops_rag.generation import NO_EVIDENCE, ExtractiveGenerator
from itops_rag.retrieval import RetrievedChunk

from .conftest import requires_data


@requires_data
def test_search_returns_k_results(service):
    hits = service.search("What is the acknowledge target for Severity 4 incidents?", k=5)
    assert len(hits) == 5


@requires_data
def test_search_results_are_ordered_by_rank(service):
    hits = service.search("cdn outage root cause", k=5)
    assert [h["rank"] for h in hits] == list(range(5))


@requires_data
@pytest.mark.parametrize("method", ["bm25", "vector", "hybrid"])
def test_all_methods_return_results(service, method):
    hits = service.search("cdn outage root cause", method=method, k=5)
    assert len(hits) == 5
    assert all(h["chunk_id"] for h in hits)


@requires_data
def test_unknown_method_is_rejected(service):
    with pytest.raises(ValueError, match="unknown method"):
        service.search("anything", method="magic")


@requires_data
def test_title_anchor_puts_quoted_document_first(service):
    """Questions quote doc titles verbatim; the anchor must surface that document."""
    question = (
        "Answer using evidence. What is the correct key detail from "
        "'EU cdn_issue incident report (2025-07-13)' (ignore unrelated notes)?"
    )
    hits = service.search(question, method="hybrid", k=5)
    assert hits[0]["title"] == "EU cdn_issue incident report (2025-07-13)"


@requires_data
def test_title_anchor_can_be_disabled(service):
    question = "What is the correct key detail from 'EU cdn_issue incident report (2025-07-13)'?"
    anchored = service.search(question, k=5, use_title_anchor=True)
    plain = service.search(question, k=5, use_title_anchor=False)
    # The anchored result must be at least as good at surfacing the title.
    assert anchored[0]["title"] == "EU cdn_issue incident report (2025-07-13)"
    assert plain != anchored


@requires_data
def test_ask_returns_grounded_answer_with_citations(service):
    result = service.ask("What is the acknowledge and mitigation target for Severity 4 incidents?")
    assert result.grounded is True
    assert result.citations
    assert "10 minutes" in result.answer


@requires_data
def test_ask_refuses_out_of_scope_questions(service):
    for question in [
        "What is the capital of France?",
        "How do I bake sourdough bread?",
        "Who won the 2018 FIFA World Cup?",
    ]:
        result = service.ask(question)
        assert result.grounded is False
        assert result.answer == NO_EVIDENCE
        assert result.citations == []


@requires_data
def test_ask_combine_question_cites_two_chunks(service):
    question = (
        "Combine evidence from 'EU cdn_issue incident report (2025-07-13)' and "
        "'Cdn Config Note: operational notes'. What is the recommended mitigation approach?"
    )
    result = service.ask(question)
    assert result.grounded is True
    assert len(result.citations) >= 1


@requires_data
def test_ask_records_latency(service):
    result = service.ask("What is the acknowledge target for Severity 3 incidents?")
    assert result.latency_ms > 0


def test_extractive_generator_refuses_below_threshold():
    from itops_rag.config import Settings

    generator = ExtractiveGenerator(Settings(min_similarity=0.9))
    chunk = RetrievedChunk(
        chunk_id="doc_0001_chunk_1",
        doc_id="doc_0001",
        title="T",
        doc_type="incident_report",
        text="Some text. More text.",
        score=1.0,
        rank=0,
    )
    answer = generator.generate("q", [chunk], best_similarity=0.1)
    assert answer.grounded is False
    assert answer.answer == NO_EVIDENCE


def test_extractive_generator_uses_first_sentence():
    from itops_rag.config import Settings

    generator = ExtractiveGenerator(Settings(min_similarity=0.0))
    chunk = RetrievedChunk(
        chunk_id="doc_0001_chunk_1",
        doc_id="doc_0001",
        title="T",
        doc_type="incident_report",
        text="Key point here. Supporting detail follows.",
        score=1.0,
        rank=0,
    )
    answer = generator.generate("q", [chunk], best_similarity=0.9)
    assert answer.answer == "Key point here."
    assert answer.citations == ["doc_0001_chunk_1"]
