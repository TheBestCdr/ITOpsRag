# Final implementation report

**Project:** IT Operations AI Knowledge Assistant (RAG)
**Source notebook:** `Caproj/it_ops_rag_assistant.ipynb`
**Repository:** https://github.com/TheBestCdr/ITOpsRag
**Date:** 2026-11-12
**Status:** Deployed to Azure and verified end-to-end.

---

## 0. Deployment summary (verified)

| Item | Value |
|---|---|
| Subscription | Visual Studio Professional Subscription (`5a9798a6-3140-4e0b-8d97-efe52f9fa6f4`) |
| Resource group | `rg-itops-rag` (eastus) |
| Public URL | https://ca-itops-67f340.kindbush-cbd7607a.eastus.azurecontainerapps.io |
| Container image | `ghcr.io/thebestcdr/itopsrag:905d7f2e8638141a4a0a958e983db1a92bc8898f` |
| Git commit | `905d7f2` (`feature/production-deployment`) |
| Chat model | `gpt-5-mini` (2025-08-07, GlobalStandard) |
| Embedding model | `text-embedding-3-small` (v1, 512 dims, Standard) |
| Indexed corpus | **1,200 documents / 3,600 chunks** |
| Azure AI Search | `srch-itops-67f340` (Free tier) |
| Container App | `ca-itops-67f340` (min 0 / max 2 replicas) |
| Observability | Application Insights `appi-itops-67f340` — **live** |
| LangSmith | Not configured (no API key supplied) |

### Model substitution (approved)

`gpt-4o-mini` version `2024-07-18` is the only version offered in every region
checked and is **deprecated** (since 2026-03-31), so Azure rejects the
deployment with `ServiceModelDeprecated`. With explicit approval, the chat
deployment was switched to **`gpt-5-mini`**. Because `gpt-5-mini` is a reasoning
model, the chat call was adapted to send `max_completion_tokens` instead of
`temperature`/`max_tokens` (see §2).

### Verified behaviour

| Check | Result |
|---|---|
| `/healthz` | `200 {"status":"ok"}` |
| `/readyz` | `200`, `index_documents: 3600`, `retriever_backend: azure` |
| `/ask` (grounded) | `200`, grounded answer with citations `doc_0348_chunk_2`, `doc_0394_chunk_2`, `doc_0426_chunk_2` |
| `/ask` without `X-API-Key` | `401` |
| `/ask` out-of-scope | `grounded: false`, no citations (abstention works) |
| `/search` BM25 / vector / hybrid | all `200` with hits |
| Rate limiting | `429` after 30 requests/minute |
| Application Insights | request telemetry confirmed (`/ask` 2 ok, `/search` 17 ok, `/readyz` 43, `/healthz` 29) |
| Cold start | ~10–20 s on first request after scale-to-zero |

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
- **Models:** `text-embedding-3-small` @ 512 dims, `gpt-5-mini` for grounded
  answers (`gpt-4o-mini` is deprecated and no longer deployable).
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
| `pytest` | ✅ 63 passed |
| Bicep compiles | ✅ `bicep build` clean, Free tier confirmed |
| React production build | ✅ 146.5 kB JS (47.4 kB gzip) |
| API live (`uvicorn`) | ✅ `/healthz`, `/readyz`, `/ask`, `/search`, `/feedback` |
| Grounded answer + citation | ✅ "acknowledge within 10 minutes…" → `doc_0348_chunk_2` |
| Refusal on out-of-scope | ✅ "How do I bake sourdough bread?" → insufficient evidence |
| UI in browser | ✅ Renders, asks, shows citations and evidence, feedback works |
| Structured JSON logs | ✅ Correlation IDs present on every request |
| Package installs without torch | ✅ Verified in a clean venv |
| **Azure deployment** | ✅ Live at the URL in §0 |
| **Azure AI Search index** | ✅ 3,600 chunks |
| **Azure OpenAI (gpt-5-mini)** | ✅ Grounded answers with citations |
| **Application Insights** | ✅ Request telemetry confirmed |

## 6. Azure deployment verification

Everything below was executed against the live deployment on 2026-11-12.

| Item | Result |
|---|---|
| Azure provisioning | ✅ `main` deployment `Succeeded`; 6 resources created |
| Azure AI Search indexing | ✅ 3,600 chunks / 1,200 documents |
| Azure OpenAI calls | ✅ embeddings + `gpt-5-mini` chat both working |
| Deployed URL | ✅ https://ca-itops-67f340.kindbush-cbd7607a.eastus.azurecontainerapps.io |
| CI/CD execution | ✅ CI built and pushed the immutable image for `905d7f2` |
| LangSmith traces | ⚠️ Not enabled — no `LANGSMITH_API_KEY` was supplied |
| Actual Azure spend | ⚠️ Billing data not yet populated at report time (see §7) |

### Issues found and fixed during deployment

| Issue | Cause | Fix |
|---|---|---|
| `ServiceModelDeprecated` | `gpt-4o-mini` 2024-07-18 is deprecated | Switched to `gpt-5-mini` (approved) |
| `Unsupported parameter: max_tokens` | Reasoning models reject `temperature`/`max_tokens` | `_completion_params()` sends `max_completion_tokens` for `gpt-5*`/`o1`/`o3`/`o4` |
| `ContainerAppSecretInvalid` | Empty secret values are rejected | Secrets/env emitted only when non-empty |
| Budget `400` | Notifications need a recipient | Switched to `contactRoles: ['Owner']` |
| Search `403 Forbidden` | Service was `apiKeyOnly`; managed identity rejected | Enabled `authOptions.aadOrApiKey` in Bicep |
| `az search service list` error | Requires `--resource-group` on current CLI | Use `az resource list` subscription-wide |
| `deploy.ps1` aborted on success | `az` warning on stderr tripped `ErrorActionPreference` | Redirect stderr on the deploy call |
| Indexer `ModuleNotFoundError` | Bare `python` resolved outside the venv | `index.ps1` prefers `.venv` |
| Indexer `403` on upload | Developer identity lacks data-plane write | `index.ps1` uses the search admin key |
| Embedding `429` | 10K TPM deployment vs 256-text batches | Adaptive batching + backoff; capacity raised to 350K TPM |

## 7. Cost

Estimated **≈ $0.32/month** at 1,000 queries; **≈ $19.20/month** at the daily
request cap for a full month. Both under the $20 target.

Index footprint: **~10 MB** (0.71 MB text + 7.37 MB vectors @ 512 dims + HNSW
overhead) — 20% of the 50 MB Free-tier cap.

**Actual spend:** the `budget-itops-67f340` budget exists ($20/month, alerts at
125% and 200%) and reported **$0.00** at report time. Azure cost data lags
resource creation by several hours, so no meaningful actual figure was
available. Re-check with:

```powershell
az consumption usage list --start-date 2026-11-01 --end-date 2026-11-30 `
  --query "[?contains(instanceName,'itops')].{name:instanceName,cost:pretaxCost}" -o table
```

See [`COST.md`](COST.md).

## 8. Remaining limitations

1. **`gpt-5-mini` replaces `gpt-4o-mini`.** The originally specified model is
   deprecated. `gpt-5-mini` is a reasoning model, so answers cost more tokens
   (reasoning tokens are billed) and latency is higher (~2.7 s per `/ask`).
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
7. **LangSmith tracing is off.** Set `LANGSMITH_API_KEY` and redeploy to enable.

## 9. Teardown

```powershell
az group delete --name rg-itops-rag --yes --no-wait
```

This removes every resource created by this deployment, including the Azure
OpenAI account and its model deployments.
