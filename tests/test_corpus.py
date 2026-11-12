"""Tests for corpus loading, full-corpus coverage, and the evaluation set."""

from __future__ import annotations

import pytest

from itops_rag.config import Settings
from itops_rag.corpus import build_eval_set, load_corpus

from .conftest import requires_data


@requires_data
def test_corpus_is_the_complete_dataset(corpus):
    """The knowledge base must be all 1,200 documents / 3,600 chunks."""
    assert corpus.chunks["doc_id"].nunique() == 1200
    assert len(corpus.chunks) == 3600


@requires_data
def test_every_document_has_three_chunks(corpus):
    per_doc = corpus.chunks.groupby("doc_id").size()
    assert set(per_doc.unique()) == {3}


@requires_data
def test_chunk_ids_are_unique(corpus):
    assert corpus.chunks["chunk_id"].is_unique


@requires_data
def test_chunk_id_prefix_matches_doc_id(corpus):
    """Citations rely on 'doc_XXXX_chunk_N' splitting back to the doc id."""
    for row in corpus.chunks.head(50).itertuples(index=False):
        assert row.chunk_id.split("_chunk")[0] == row.doc_id


@requires_data
def test_eval_set_has_no_duplicate_questions(corpus):
    eval_set = build_eval_set(corpus)
    assert eval_set["question"].is_unique


@requires_data
def test_eval_set_evidence_is_inside_the_corpus(corpus):
    eval_set = build_eval_set(corpus)
    doc_ids = corpus.doc_ids
    for gold in eval_set["gold_chunk_ids"]:
        for chunk_id in gold:
            assert chunk_id.split("_chunk")[0] in doc_ids


@requires_data
def test_eval_set_size_matches_baseline(corpus):
    """691 distinct evidence-supported questions, as measured in the notebook."""
    assert len(build_eval_set(corpus)) == 691


def test_missing_data_raises_clear_error(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(FileNotFoundError, match="Kaggle AgentEval"):
        load_corpus(settings)


def test_wrong_corpus_size_is_rejected(tmp_path):
    """A sampled corpus must fail validation, not silently shrink the index."""

    (tmp_path / "corpus.csv").write_text(
        "chunk_id,doc_id,title,doc_type,chunk_text\n"
        "doc_0001_chunk_1,doc_0001,T,incident_report,text\n"
    )
    (tmp_path / "train.csv").write_text("question,answer,gold_chunk_ids\nq,a,\"['doc_0001_chunk_1']\"\n")
    (tmp_path / "test.csv").write_text("question_id,question\nq_1,q\n")

    settings = Settings(data_dir=tmp_path)
    with pytest.raises(ValueError, match="expected 1200"):
        load_corpus(settings)
