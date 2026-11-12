"""Idempotent indexing CLI: build the Azure AI Search index from the corpus.

Usage:
    python -m itops_rag.index --recreate
    python -m itops_rag.index            # create/update schema, then upsert

Re-running is safe: documents are keyed by chunk_id and uploaded with
merge_or_upload, so the index converges to the corpus contents.
"""

from __future__ import annotations

import argparse
import logging

from .config import get_settings
from .corpus import load_corpus
from .observability import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Index the corpus into Azure AI Search")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and rebuild the index schema before uploading",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)

    from .azure_openai import AzureOpenAIEmbedder
    from .azure_search import _index_client, build_index, corpus_fingerprint, index_corpus

    corpus = load_corpus(settings)
    logger.info(
        "corpus loaded: %d documents, %d chunks (fingerprint %s)",
        corpus.chunks["doc_id"].nunique(),
        len(corpus.chunks),
        corpus_fingerprint(corpus.chunks),
    )

    if args.recreate:
        client = _index_client(settings)
        try:
            client.delete_index(settings.azure_search_index)
            logger.info("deleted existing index %s", settings.azure_search_index)
        except Exception:
            logger.info("no existing index to delete")

    build_index(settings)

    embedder = AzureOpenAIEmbedder(settings)
    uploaded = index_corpus(settings, corpus.chunks, embedder, batch_size=args.batch_size)

    logger.info("indexing complete: %d chunks uploaded", uploaded)
    print(f"Indexed {uploaded} chunks into '{settings.azure_search_index}'.")


if __name__ == "__main__":
    main()
