# Evaluation

## Method

The evaluation set is built from `train.csv` with two safeguards:

1. **Duplicate question text is removed.** The same question appears many times
   with different gold chunks; keeping all rows would inflate scores.
2. **Questions whose gold evidence is outside the corpus are dropped.** With the
   full corpus this removes nothing, but the check stays so the metric is honest
   if the data changes.

Result: **691 distinct questions**, 305 needing one evidence chunk and 386
needing two.

Reference answers are used **only** for scoring. They are never placed in the
retrieval context, and the hidden competition answers in `test.csv` are never
used for evaluation.

## Metrics

| Metric | Definition |
|---|---|
| Recall@5 | Fraction of gold evidence chunks appearing in the top 5 |
| MRR | Mean of 1/rank of the first correct chunk |
| Token F1 | Word-overlap F1 between generated and reference answer |
| Citation precision (chunk) | Share of cited chunks that are gold evidence |
| Citation recall (chunk) | Share of gold chunks that were cited |
| Citation precision (document) | Same, but matching at document level |
| Citation recall (document) | Same, but matching at document level |
| Abstention rate | Share of questions refused for insufficient evidence |
| Latency | Wall-clock ms per question, mean and p95 |
| Token cost | Prompt and completion tokens consumed |

## Results — local backend (notebook parity)

Reproduce with:

```bash
export DATA_DIR=/path/to/agent-eval-part-i-grounded-rag-benchmark
python -m itops_rag.evaluate --out eval/reports/local.json
```

### Retrieval

| Method | Recall@5 | MRR | Recall@5 (no title anchor) | MRR (no title anchor) |
|---|---|---|---|---|
| BM25 | 0.7656 | 0.5770 | 0.4240 | 0.4050 |
| Vector | 0.7395 | 0.5538 | 0.2260 | 0.1470 |
| **Hybrid** | **0.7721** | **0.5745** | **0.4410** | **0.4030** |

### Answer quality (hybrid)

| Metric | Value |
|---|---|
| Token F1 | 0.3290 |
| Citation precision (chunk) | 0.2902 |
| Citation recall (chunk) | 0.2902 |
| Citation precision (document) | 0.9768 |
| Citation recall (document) | 0.7135 |
| Abstention rate | 0.0000 |
| Latency mean | 15.6 ms |
| Latency p95 | 20.9 ms |

These match the notebook baseline exactly, which confirms the extraction into a
package did not change behaviour.

## Interpreting the citation metrics

Chunk-level citation precision is 0.29 while document-level precision is 0.977.
This gap is a property of the dataset, not a defect:

- The corpus repeats identical passages across many documents. One security
  advisory title appears in **468 documents**.
- When the assistant cites `doc_0015_chunk_3` and the gold answer is
  `doc_0021_chunk_3` with byte-identical text, the citation is correct in
  substance but scores zero at chunk level.
- Document-level matching captures what actually matters: did the answer come
  from the right source document?

Report document-level precision as the headline citation metric.

## Title anchoring

Questions in this dataset quote document titles verbatim:

> "Answer using evidence. What is the correct key detail from
> **'EU cdn_issue incident report (2025-07-13)'** (ignore unrelated notes)?"

Boosting chunks whose title appears in the question is the single largest quality
win:

| Method | Recall@5 without anchor | Recall@5 with anchor | Delta |
|---|---|---|---|
| BM25 | 0.424 | 0.766 | **+0.342** |
| Vector | 0.226 | 0.740 | **+0.514** |
| Hybrid | 0.441 | 0.772 | **+0.331** |

Vector search benefits most, because embeddings alone do not reliably surface an
exact title match among 3,600 chunks.

## Comparing backends

Run both and diff the JSON reports:

```bash
python -m itops_rag.evaluate --out eval/reports/local.json
python -m itops_rag.evaluate --backend azure --generator azure_openai \
  --out eval/reports/azure.json

python - <<'PY'
import json
local = json.load(open("eval/reports/local.json"))
azure = json.load(open("eval/reports/azure.json"))
for a, b in zip(local["retrieval"], azure["retrieval"]):
    print(f"{a['method']:8} R@5 {a['recall_at_k']:.3f} -> {b['recall_at_k']:.3f}  "
          f"MRR {a['mrr']:.3f} -> {b['mrr']:.3f}")
PY
```

Expected differences:

- **BM25** will shift, because Azure uses Lucene BM25 with different parameters.
- **Vector** should be close if both use the same embedding model. The local
  backend uses MiniLM-384; Azure uses `text-embedding-3-small` @ 512, so expect
  a real difference — this is a model change, not a bug.
- **Hybrid** should be the most stable, since RRF is rank-based.

Record the actual numbers in the final report. Do not assume parity.

## Reproducibility

- Fixed corpus and fixed evaluation set (691 questions, deterministic).
- `temperature=0` for LLM generation.
- No random sampling anywhere in the pipeline.
- Reports are JSON, so runs can be diffed in CI.
