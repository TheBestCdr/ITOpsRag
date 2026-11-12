"""Shared pytest fixtures.

Tests run against the real corpus when it is available (so retrieval behaviour is
genuinely verified) and skip cleanly when the Kaggle data is absent, which is the
case in a fresh clone because the data is never committed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from itops_rag.config import Settings
from itops_rag.corpus import Corpus, load_corpus

DATA_DIR = Path(
    os.environ.get(
        "DATA_DIR",
        "/workspaces/ICS_DL/Caproj/agent-eval-part-i-grounded-rag-benchmark",
    )
)


def _data_available() -> bool:
    return (DATA_DIR / "corpus.csv").exists()


requires_data = pytest.mark.skipif(
    not _data_available(), reason="Kaggle corpus not present (data is never committed)"
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        data_dir=DATA_DIR,
        retriever_backend="local",
        generator_backend="extractive",
        embedding_backend="local",
        api_key=None,
        kill_switch=False,
        rate_limit_per_minute=1000,
        daily_request_cap=100000,
    )


@pytest.fixture(scope="session")
def corpus(settings: Settings) -> Corpus:
    if not _data_available():
        pytest.skip("Kaggle corpus not present")
    return load_corpus(settings)


@pytest.fixture(scope="session")
def service(settings: Settings, corpus: Corpus):
    from itops_rag.service import RagService

    return RagService(settings, corpus)
