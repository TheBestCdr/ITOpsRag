"""Reproducible evaluation of the three retrieval strategies.

Runs BM25, vector, and hybrid retrieval on the same held-out questions and
reports Recall@5, MRR, citation correctness, groundedness, latency, and token
cost. Writes a JSON report so results can be compared across runs.

Usage:
    python -m itops_rag.evaluate --backend local --out eval/reports/local.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np

from .config import Settings, get_settings
from .corpus import build_eval_set, load_corpus
from .service import RagService
from .text import token_f1

METHODS = ("bm25", "vector", "hybrid")


def evaluate_retrieval(service: RagService, eval_set, method: str, k: int = 5) -> dict:
    """Recall@k and MRR for one retrieval method."""
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []

    for _, row in eval_set.iterrows():
        hits = service.search(row["question"], method=method, k=k)
        retrieved = [h["chunk_id"] for h in hits]
        gold = set(row["gold_chunk_ids"])

        flags = [1 if c in gold else 0 for c in retrieved]
        recalls.append(sum(flags) / len(gold))
        reciprocal_ranks.append(
            next((1 / (rank + 1) for rank, hit in enumerate(flags) if hit), 0.0)
        )

    return {
        "method": method,
        "recall_at_k": round(float(np.mean(recalls)), 4),
        "mrr": round(float(np.mean(reciprocal_ranks)), 4),
        "n_questions": len(recalls),
    }


def evaluate_answers(service: RagService, eval_set, method: str = "hybrid") -> dict:
    """Answer quality, citation correctness, groundedness, latency, token cost."""
    rows = []
    for _, row in eval_set.iterrows():
        start = time.perf_counter()
        result = service.ask(row["question"], method=method)
        latency_ms = (time.perf_counter() - start) * 1000

        gold = set(row["gold_chunk_ids"])
        gold_docs = {g.split("_chunk")[0] for g in gold}
        cited = result.citations

        rows.append(
            {
                "f1": token_f1(result.answer, row["answer"]),
                "chunk_precision": sum(c in gold for c in cited) / len(cited) if cited else 0.0,
                "chunk_recall": sum(c in cited for c in gold) / len(gold),
                "doc_precision": (
                    sum(c.split("_chunk")[0] in gold_docs for c in cited) / len(cited)
                    if cited
                    else 0.0
                ),
                "doc_recall": (
                    sum(any(c.split("_chunk")[0] == g for c in cited) for g in gold_docs)
                    / len(gold_docs)
                ),
                "latency_ms": latency_ms,
                "abstained": not result.grounded,
                "prompt_tokens": result.usage.get("prompt_tokens", 0),
                "completion_tokens": result.usage.get("completion_tokens", 0),
            }
        )

    def mean(key: str) -> float:
        return round(float(np.mean([r[key] for r in rows])), 4)

    latencies = [r["latency_ms"] for r in rows]
    return {
        "method": method,
        "n_questions": len(rows),
        "token_f1": mean("f1"),
        "citation_precision_chunk": mean("chunk_precision"),
        "citation_recall_chunk": mean("chunk_recall"),
        "citation_precision_doc": mean("doc_precision"),
        "citation_recall_doc": mean("doc_recall"),
        "abstention_rate": mean("abstained"),
        "latency_ms_mean": round(statistics.mean(latencies), 1),
        "latency_ms_p95": round(float(np.percentile(latencies, 95)), 1),
        "prompt_tokens_total": int(sum(r["prompt_tokens"] for r in rows)),
        "completion_tokens_total": int(sum(r["completion_tokens"] for r in rows)),
    }


def run(settings: Settings, out_path: Path | None = None) -> dict:
    """Run the full evaluation and optionally write a JSON report."""
    corpus = load_corpus(settings)
    eval_set = build_eval_set(corpus)
    service = RagService(settings, corpus)

    report = {
        "backend": settings.retriever_backend,
        "generator": settings.generator_backend,
        "embedding_backend": settings.embedding_backend,
        "corpus": {
            "documents": int(corpus.chunks["doc_id"].nunique()),
            "chunks": int(len(corpus.chunks)),
        },
        "eval_questions": int(len(eval_set)),
        "retrieval": [evaluate_retrieval(service, eval_set, m) for m in METHODS],
        "answers": evaluate_answers(service, eval_set, "hybrid"),
    }

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2))

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the IT Ops RAG assistant")
    parser.add_argument("--backend", choices=["local", "azure"], default=None)
    parser.add_argument("--generator", choices=["extractive", "azure_openai"], default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()
    if args.backend:
        settings = settings.model_copy(update={"retriever_backend": args.backend})
    if args.generator:
        settings = settings.model_copy(update={"generator_backend": args.generator})

    report = run(settings, args.out)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
