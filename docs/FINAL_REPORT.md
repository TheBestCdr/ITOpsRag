# Final implementation report

**Project:** IT Operations AI Knowledge Assistant (RAG)
**Source notebook:** `Caproj/it_ops_rag_assistant.ipynb`
**Repository:** https://github.com/TheBestCdr/ITOpsRag
**Date:** 2026-11-12

---

## 1. What was reused from the notebook

The notebook's retrieval and answering logic was extracted into a package
**without changing its behaviour**. Verified by re-running the evaluation: every
metric matches the notebook baseline to four decimal places.

| Reused verbatim | Location |
|---|---|
| `tokenize`, `split_sentences`, `token_f1` | `src/itops_rag/text.py` |
| `BM25` class (k1=1.5, b=0.75) | `src/itops_rag/bm25.py` |
| `reciprocal_rank_fusion`, `top_indices` | `src/itops_rag/retrieval.py` |
| Title anchoring (`QUOTED_RE`, exact then substring match) | `src/itops_rag/retrieval.py` |
| Extractive answerer, severity-clause extraction, "combine" handling | `src/itops_rag/generation.py` |
| Abstention rule (`min_similarity = 0.35`) | `config.py` + `generation.py` |
| Evaluation loop and metric definitions | `src/itops_rag/evaluate.py` |
| Full-corpus validation (1,200 docs / 3,600 chunks) | `src/itops_rag/corpus.py` |

The notebook itself is **untouched**.

## 2. What was refactored or added

### Refactored

| Change | Reason |
|---|---|
| Hardcoded `DATA_DIR` → `Settings.data_dir` env var | Same image runs locally, in CI, and in Azure |
| `submission.csv` write → removed from the service | Serving and batch submission are separate concerns |
| Monolithic cells → 12 focused modules | Testable, mockable, single responsibility |
| `search()` positional-arg footgun → keyword-only `k` | The notebook's `search(q, method, k, use_title_anchor)` silently broke when called positionally |
| `Retriever` / `Generator` protocols | Two backends each, swappable by config |

### Added

| Component | Purpose |
|---|---|
| `AzureSearchRetriever` | BM25 / vector / hybrid against Azure AI Search |
| `AzureOpenAIEmbedder` | `text-embedding-3-small` @ 512 dims |
| `AzureOpenAIGenerator` | Grounded LLM answers with `[n]` → chunk-ID mapping |
| `/search`, `/readyz`, `/feedback` | Inspection, readiness, feedback |
| API-key auth, rate limiting, daily cap, kill switch | Abuse prevention |
| Request correlation IDs + JSON logs | Traceability |
| React + Vite UI | Ask, view evidence, submit feedback |
| Dockerfile (multi-stage, non-root, no torch) | Small image, fast cold start |
| Bicep infrastructure | Free tier, scale-to-zero, budget alerts |
| GitHub Actions CI + OIDC deploy | Lint, type check, test, build, scan, deploy, smoke test |
| Evaluation harness with JSON reports | Reproducible, diffable results |
| 62 tests | Unit, integration, API, and fake-client Azure tests |

## 3. Final architecture

Single Docker image on Azure Container Apps (Consumption, `minReplicas=0`,
`maxReplicas=2`), serving a React SPA and a FastAPI backend from one container.

- **Retrieval:** Azure AI Search Free (3,600 chunks) with BM25, vector, and RRF
  hybrid; `LocalRetriever` retained as the evaluation baseline and no-Azure
  fallback.
- **Models:** `text-embedding-3-small` @ 512 dims, `gpt-4o-mini` for grounded
  answers.
- **Observability:** structured JSON logs → Log Analytics (1 GB/day cap),
  Application Insights, optional LangSmith.
- **Identity:** system-assigned managed identity; no credentials in source.

See [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 4. Evaluation vs. notebook baseline

**Identical.** 691 distinct questions, full 1,200-document corpus.

| Metric | Notebook | Package | Delta |
|---|---|---|---|
| BM25 Recall@5 | 0.766 | 0.7656 | 0.000 |
| Vector Recall@5 | 0.740 | 0.7395 | 0.000 |
| Hybrid Recall@5 | 0.772 | 0.7721 | 0.000 |
| BM25 MRR | 0.577 | 0.5770 | 0.000 |
| Vector MRR | 0.554 | 0.5538 | 0.000 |
| Hybrid MRR | 0.574 | 0.5745 | 0.000 |
| Token F1 | 0.329 | 0.3290 | 0.000 |
| Citation precision (doc) | 0.977 | 0.9768 | 0.000 |
| Citation recall (doc) | 0.713 | 0.7135 | 0.000 |
| Latency mean | 21.4 ms | 15.6 ms | faster (warm cache) |

Reproduce: `python -m itops_rag.evaluate --out eval/reports/local.json`

See [`EVALUATION.md`](EVALUATION.md) for the Azure-backend comparison procedure.

## 5. Verification performed

| Check | Result |
|---|---|
| Full corpus indexed (local) | ✅ 1,200 docs / 3,600 chunks |
| `ruff check src tests` | ✅ All checks passed |
| `mypy src` | ✅ No issues in 14 source files |
| `pytest` | ✅ 62 passed |
| Bicep compiles | ✅ `bicep build` clean, Free tier confirmed |
| React production build | ✅ 146.5 kB JS (47.4 kB gzip) |
| API live (`uvicorn`) | ✅ `/healthz`, `/readyz`, `/ask`, `/search`, `/feedback` |
| Grounded answer + citation | ✅ "acknowledge within 10 minutes…" → `doc_0793_chunk_2` |
| Refusal on out-of-scope | ✅ "How do I bake sourdough bread?" → insufficient evidence |
| UI in browser | ✅ Renders, asks, shows citations and evidence, feedback works |
| Structured JSON logs | ✅ Correlation IDs present on every request |
| Package installs without torch | ✅ Verified in a clean venv |

## 6. Not verified — blockers

These items could not be completed in this environment and are **not** claimed as
done:

| Item | Blocker |
|---|---|
| `docker build` / `docker run` | Container lacks `unshare` and bind-mount privileges. Dockerfile authored and its install steps verified natively in a clean venv instead. |
| Azure provisioning | No Azure credentials in this environment (`az login` required). |
| Azure AI Search indexing | Requires a provisioned search service. |
| Azure OpenAI calls | Requires a resource with model quota. |
| Deployed URL | Requires provisioning. |
| CI/CD execution | Requires a GitHub token and a pushed branch. |
| LangSmith / App Insights traces | Requires credentials. |
| Actual Azure spend | Requires a subscription. |

## 7. Cost

Estimated **≈ $0.32/month** at 1,000 queries; **≈ $19.20/month** at the daily
request cap for a full month. Both under the $20 target.

Index footprint: **~10 MB** (0.71 MB text + 7.37 MB vectors @ 512 dims + HNSW
overhead) — 20% of the 50 MB Free-tier cap.

See [`COST.md`](COST.md).

## 8. Remaining limitations

1. **Extractive answers are terse.** The default generator returns the first
   sentence of the top chunk. `GENERATOR_BACKEND=azure_openai` produces fluent
   answers, but that path is unverified here (no Azure credentials).
2. **Chunk-level citation precision is 0.29.** This is a dataset property — the
   same passage appears in up to 468 documents. Document-level precision (0.977)
   is the meaningful metric.
3. **Rate limiting is per-replica and in-memory.** Fine for one replica; a
   multi-replica deployment needs Redis or API Management.
4. **The 0.35 abstention threshold is dataset-specific.** Recalibrate for a
   different corpus.
5. **Azure AI Search Free is shared infrastructure** and may be deleted after
   extended inactivity. Recovery is one idempotent command.
6. **The corpus is not in the image.** Indexing requires the Kaggle data on the
   machine running the indexer. This is deliberate — the data must not be
   committed.

## 9. Manual steps required to finish

1. `az login` and select the VS Professional subscription; confirm the spending
   limit is enabled.
2. Confirm `gpt-4o-mini` and `text-embedding-3-small` deployments exist with
   quota.
3. Confirm no Free Azure AI Search service already exists in the subscription.
4. Push the branch and open a PR (no GitHub credentials here).
5. Run `bash infra/deploy.sh`, then `python -m itops_rag.index --recreate`.
6. Grant the app identity `Cognitive Services OpenAI User` on the Azure OpenAI
   resource.
7. Configure the OIDC federated credential and repository secrets.
8. Run `python -m itops_rag.evaluate --backend azure --generator azure_openai`
   and record the Azure numbers alongside the local baseline.
9. Record actual spend from `az consumption usage list`.
