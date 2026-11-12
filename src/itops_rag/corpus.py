"""Corpus loading and validation.

The knowledge base is the complete Kaggle AgentEval corpus: every ``doc_id``
group in ``corpus.csv`` (1,200 documents / 3,600 chunks). No sampling.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import Settings


@dataclass(frozen=True)
class Corpus:
    """The chunked knowledge base plus the labelled question sets."""

    chunks: pd.DataFrame
    train: pd.DataFrame
    test: pd.DataFrame

    @property
    def doc_ids(self) -> set[str]:
        return set(self.chunks["doc_id"])

    @property
    def chunk_ids(self) -> list[str]:
        return self.chunks["chunk_id"].tolist()


def _parse_gold(value: object) -> list[str]:
    """``gold_chunk_ids`` is stored as a string like "['doc_0001_chunk_1']"."""
    if isinstance(value, list):
        return value
    return ast.literal_eval(str(value))


def load_corpus(settings: Settings, *, validate: bool = True) -> Corpus:
    """Load corpus.csv / train.csv / test.csv and validate the full corpus.

    Raises:
        FileNotFoundError: if a required file is missing.
        ValueError: if the corpus does not match the expected full size.
    """
    data_dir = Path(settings.data_dir)
    corpus_path = data_dir / "corpus.csv"
    train_path = data_dir / "train.csv"
    test_path = data_dir / "test.csv"

    for path in (corpus_path, train_path, test_path):
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}. Download the Kaggle AgentEval Part I dataset "
                f"and point DATA_DIR at it."
            )

    chunks = pd.read_csv(corpus_path)
    train = pd.read_csv(train_path)
    test = pd.read_csv(test_path)

    train["gold_chunk_ids"] = train["gold_chunk_ids"].apply(_parse_gold)

    if validate:
        n_docs = chunks["doc_id"].nunique()
        if n_docs != settings.expected_docs:
            raise ValueError(
                f"corpus.csv has {n_docs} documents, expected {settings.expected_docs}. "
                "The knowledge base must be the complete corpus."
            )
        if len(chunks) != settings.expected_chunks:
            raise ValueError(
                f"corpus.csv has {len(chunks)} chunks, expected {settings.expected_chunks}."
            )

    return Corpus(chunks=chunks, train=train, test=test)


def build_eval_set(corpus: Corpus) -> pd.DataFrame:
    """One row per distinct question whose gold evidence is inside the corpus.

    Duplicate question text is removed so scores are not inflated, and any
    question whose evidence is missing from the knowledge base is dropped.
    """
    doc_ids = corpus.doc_ids

    def evidence_in_kb(gold_ids: list[str]) -> bool:
        return all(g.split("_chunk")[0] in doc_ids for g in gold_ids)

    train = corpus.train.copy()
    train["in_kb"] = train["gold_chunk_ids"].apply(evidence_in_kb)

    return (
        train[train["in_kb"]]
        .drop_duplicates("question")
        .reset_index(drop=True)
    )
