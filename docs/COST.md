# Cost model

Target: **≤ $20/month** for development and testing on a Visual Studio
Professional subscription.

All prices are from the official Azure pricing pages (retrieved 2026-11-12) and
are US-dollar list prices. They are estimates, not quotes.

## Configuration

| Service | Configuration | Why |
|---|---|---|
| Azure AI Search | **Free** tier | 50 MB, 3 indexes, 1 per subscription. Enough for 3,600 chunks. |
| Azure OpenAI embeddings | `text-embedding-3-small` @ 512 dims | Cheapest embedding model; 512 dims keeps the index small. |
| Azure OpenAI chat | `gpt-5-mini` | `gpt-4o-mini` is deprecated and cannot be deployed. |
| Container Apps | Consumption, 0.5 vCPU / 1 GiB, `minReplicas=0`, `maxReplicas=2` | Scales to zero; free grant covers light use. |
| Container Registry | **GHCR** (public) | Avoids ACR's ~$5/month. |
| Log Analytics | 1 GB/day cap | Free grant is 5 GB/month. |
| LangSmith | Developer free tier | 5,000 traces/month. |

> **Cost impact of the model change:** `gpt-5-mini` is a reasoning model, so
> each answer also bills hidden reasoning tokens. Measured on the live
> deployment, one `/ask` used **368 prompt + 164 completion tokens** (532 total)
> and took **~2.7 s**. At 1,000 queries/month this is still well under $1, but
> it is more expensive per answer than the original `gpt-4o-mini` estimate.

## Unit prices used

| Item | Price |
|---|---|
| Container Apps active vCPU | $0.000024 / vCPU-second |
| Container Apps active memory | $0.000003 / GiB-second |
| Container Apps requests | $0.40 / million (first 2M free) |
| Container Apps free grant | 180,000 vCPU-s, 360,000 GiB-s, 2M requests / month |
| `text-embedding-3-small` | $0.000022 / 1K tokens |
| `gpt-5-mini` input | $0.25 / 1M tokens |
| `gpt-5-mini` output | $2.00 / 1M tokens |
| Azure AI Search Free | $0.00 |
| Log Analytics | 5 GB/month free, then ~$2.76/GB |

## Index footprint

Measured from the actual corpus:

| Component | Size |
|---|---|
| Chunk text (495,035 chars) | 0.50 MB |
| Titles + chunk IDs | 0.21 MB |
| Vectors @ 512 dims, float32 (3,600 × 512 × 4 B) | 7.37 MB |
| HNSW graph overhead (approx.) | ~2 MB |
| **Estimated total** | **~10 MB** |

That is **20% of the 50 MB Free-tier cap**, leaving room for index growth and
the search service's own metadata.

For comparison: 1536-dim vectors would be 22.1 MB, pushing the total to ~25 MB —
still under the cap but with much less headroom.

## Monthly estimate

Assumes ~1,000 queries/month, average 1,650 tokens per query (5 chunks of
evidence + system prompt + answer).

| Service | Calculation | Monthly |
|---|---|---|
| Azure AI Search | Free tier | **$0.00** |
| Embeddings (one-time index) | 124K tokens × $0.000022/1K | **$0.003** |
| Embeddings (queries) | 1,000 × 20 tokens × $0.000022/1K | **$0.0004** |
| Chat input | 1,000 × 368 tok × $0.25/1M | **$0.09** |
| Chat output (incl. reasoning) | 1,000 × 164 tok × $2.00/1M | **$0.33** |
| Container Apps | ~5,000 vCPU-s, ~10,000 GiB-s — inside free grant | **$0.00** |
| Log Analytics | < 1 GB/month — inside free grant | **$0.00** |
| Container Registry | GHCR public | **$0.00** |
| LangSmith | Free tier | **$0.00** |
| **Total** | | **≈ $0.42** |

Token counts are measured from the live deployment, not assumed.

## Sensitivity

| Scenario | Queries/month | Estimated cost |
|---|---|---|
| Light testing | 100 | ~$0.05 |
| Normal development | 1,000 | ~$0.42 |
| Heavy testing | 10,000 | ~$4.20 |
| Stress test | 50,000 | ~$21.00 |
| Worst case (all limits hit) | 60,000 (daily cap × 30) | ~$25.20 |

> With `gpt-5-mini` the stress and worst-case scenarios now exceed the $20
> target. The daily request cap (`DAILY_REQUEST_CAP=2000`) and the per-minute
> rate limit are the controls that keep normal use far below it.

## Guardrails in place

| Guardrail | Where |
|---|---|
| Search pinned to Free tier | `infra/main.bicep` → `sku.name: 'free'` |
| Scale to zero | `infra/main.bicep` → `minReplicas: 0` |
| Max 2 replicas | `infra/main.bicep` → `maxReplicas: 2` |
| Log ingestion cap | `infra/main.bicep` → `workspaceCapping.dailyQuotaGb` |
| Budget alerts | `infra/main.bicep` → $25 (125%) and $40 (200%) of a $20 budget |
| Output token cap | `MAX_OUTPUT_TOKENS=400` |
| Request rate limit | `RATE_LIMIT_PER_MINUTE=30` |
| Daily request cap | `DAILY_REQUEST_CAP=2000` |
| Emergency stop | `KILL_SWITCH=true` |

## What is deliberately NOT used

| Service | Why not |
|---|---|
| Azure Container Registry | ~$5/month; GHCR is free for public images |
| Azure AI Search Basic | ~$75/month; Free tier is sufficient at this scale |
| Provisioned throughput (PTU) | Fixed hourly cost; unnecessary at this volume |
| Semantic ranker | Free tier supports it but it is not needed; BM25 + vector + RRF already meets targets |
| Dedicated Container Apps plan | Fixed management fee |

## Verifying actual spend

```bash
az consumption usage list \
  --start-date 2026-11-01 \
  --end-date 2026-11-30 \
  --query "[?contains(instanceName,'itops')].{name:instanceName, cost:pretaxCost, currency:currency}" \
  --output table
```

Compare against the estimate above and record the result in the final report.
