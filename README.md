# IT Operations AI Knowledge Assistant

A grounded Retrieval-Augmented Generation (RAG) assistant for IT operations. It
answers questions about incidents, SLA policies, architecture notes, and security
advisories, and cites the exact knowledge-base passages that support each answer.
When the knowledge base cannot support an answer, it says so instead of guessing.

Built from the capstone notebook `Caproj/it_ops_rag_assistant.ipynb` and deployed
as a single Dockerized application on Azure Container Apps.

---

## What it does

| Endpoint | Purpose |
|---|---|
| `POST /ask` | Grounded answer with validated chunk citations |
| `POST /search` | Retrieval results only, for inspection |
| `POST /feedback` | Record whether an answer was helpful |
| `GET /healthz` | Liveness probe |
| `GET /readyz` | Readiness probe (corpus size, backends) |
| `GET /` | React UI |

The UI lets you ask a question, switch retrieval strategy, read the cited
evidence, and submit feedback.

---

## Knowledge base

The **complete Kaggle AgentEval Part I corpus**:

- **1,200 documents**, **3,600 chunks** (3 chunks per document)
- 8 document types: incident reports, SLA policies, architecture notes, security
  advisories, deployment logs, release notes, CDN config notes, regional outage
  summaries
- Original `doc_id` and `chunk_id` values are preserved, so citations match the
  notebook exactly

No sampling. The loader asserts the full corpus is present and fails loudly if a
subset is supplied.

---

## Retrieval strategies

Three strategies are implemented and evaluated on the same held-out questions:

1. **BM25** — keyword ranking over title + chunk text
2. **Vector** — cosine similarity over sentence embeddings
3. **Hybrid** — Reciprocal Rank Fusion of the two rankings

A **title-anchoring** boost is applied on top: questions in this dataset quote
document titles verbatim (e.g. `'EU cdn_issue incident report (2025-07-13)'`), so
chunks whose title appears in the question are promoted. This is the single
largest quality win in the project.

---

## Evaluation results

Measured on **691 distinct evidence-supported questions** from `train.csv`
(duplicate question text removed; every gold chunk is inside the corpus).

### Retrieval

| Method | Recall@5 | MRR | Recall@5 (no title anchor) |
|---|---|---|---|
| BM25 | 0.766 | 0.577 | 0.424 |
| Vector | 0.740 | 0.554 | 0.226 |
| **Hybrid** | **0.772** | **0.574** | **0.441** |

### Answer quality (hybrid retrieval)

| Metric | Value |
|---|---|
| Token F1 | 0.329 |
| Citation precision (chunk) | 0.290 |
| Citation recall (chunk) | 0.290 |
| **Citation precision (document)** | **0.977** |
| Citation recall (document) | 0.713 |
| Abstention rate | 0.000 |
| Latency mean / p95 | 15.6 ms / 20.9 ms |

**Why document-level citation scores matter here.** The corpus repeats the same
passage across many documents (up to 468 documents share one advisory title).
Citing a different chunk that holds identical text is not a wrong citation, so
chunk-ID matching is unfairly strict. Document-level precision of 0.977 is the
honest measure.

These numbers are **identical to the notebook baseline**, which is verified by
`tests/test_corpus.py` and reproducible with `python -m itops_rag.evaluate`.

---

## Architecture

```mermaid
flowchart LR
    U[Browser] -->|HTTPS| CA[Azure Container Apps<br/>min=0, max=2]
    subgraph CA[Single container]
        F[React build<br/>served by FastAPI]
        A[FastAPI<br/>/ask /search /healthz /readyz]
        R[Retriever<br/>BM25 | vector | hybrid]
        G[Generator<br/>grounded + citations]
    end
    A --> R
    R --> S[(Azure AI Search Free<br/>3,600 chunks)]
    R --> O[Azure OpenAI<br/>text-embedding-3-small]
    G --> O2[Azure OpenAI<br/>gpt-5-mini]
    A -.traces.-> LS[LangSmith]
    A -.OTLP.-> AI[App Insights]
```

**Dual retriever design.** Azure AI Search's Lucene BM25 is not the same algorithm
as the notebook's hand-written BM25, so scores shift. Two backends implement one
`Retriever` protocol:

- `LocalRetriever` — exact notebook logic (BM25 + MiniLM + RRF). Used for offline
  evaluation and as a no-Azure fallback.
- `AzureSearchRetriever` — BM25 / vector / hybrid against Azure AI Search.

The evaluation harness runs against either, so regressions are visible rather
than hidden.

---

## Quick start (local, no Azure)

```bash
# 1. Install with the local extras
python -m venv .venv && source .venv/bin/activate
pip install -e ".[local,dev]"

# 2. Point at the Kaggle data (never committed)
export DATA_DIR=/path/to/agent-eval-part-i-grounded-rag-benchmark

# 3. Run the API
uvicorn itops_rag.api:app --reload --port 8000

# 4. In another terminal, build the UI
cd web && npm install && npm run build && cd ..
# FastAPI now serves the UI at http://127.0.0.1:8000/
```

Verify:

```bash
curl -s localhost:8000/readyz
curl -s -X POST localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the acknowledge and mitigation target for Severity 4 incidents?"}'
```

---

## Tests

```bash
export DATA_DIR=/path/to/agent-eval-part-i-grounded-rag-benchmark
pytest -q
ruff check src tests
mypy src
```

62 tests cover text helpers, BM25, RRF, corpus validation, retrieval, the
extractive and Azure OpenAI generators, the Azure Search retriever (with fake
clients), the API (auth, rate limiting, kill switch, correlation IDs), and the
evaluation harness. Data-dependent tests skip cleanly when the corpus is absent,
which is the case in CI because the data is never committed.

---

## Evaluation

```bash
# Local backend (notebook parity)
python -m itops_rag.evaluate --out eval/reports/local.json

# Azure backend (after indexing)
python -m itops_rag.evaluate --backend azure --generator azure_openai \
  --out eval/reports/azure.json
```

Reports are JSON so two runs can be diffed directly.

---

## Deployment

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full runbook, and
[`docs/COST.md`](docs/COST.md) for the cost model.

```bash
az login
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
bash infra/deploy.sh
python -m itops_rag.index --recreate
```

---

## Repository layout

```
src/itops_rag/       Python package (config, corpus, retrieval, generation, API)
web/                 React + Vite frontend
tests/               pytest suite
eval/                Evaluation harness and reports
infra/               Bicep templates and deploy/teardown scripts
docs/                Architecture, deployment, cost, evaluation, teardown
.github/workflows/   CI and deploy pipelines
```

---

## Data and licensing

The Kaggle AgentEval Part I dataset is **CC BY 4.0**. It is **not** committed to
this repository; `.gitignore` and a CI check both enforce that. Download it from
[Kaggle](https://www.kaggle.com/competitions/agent-eval-part-i-grounded-rag-benchmark/data)
and point `DATA_DIR` at it.

---

## Limitations

- Answers are extractive by default (no LLM), so they are faithful but terse.
  Setting `GENERATOR_BACKEND=azure_openai` swaps in a grounded LLM answer.
- The 0.35 similarity threshold for refusal was tuned on this dataset and would
  need recalibration for a different corpus.
- The rate limiter is in-memory, so it is per-replica. A multi-replica production
  deployment should move it to Redis or API Management.
- Azure AI Search Free is shared infrastructure and may be deleted after extended
  inactivity. Re-indexing is idempotent, so recovery is one command.
