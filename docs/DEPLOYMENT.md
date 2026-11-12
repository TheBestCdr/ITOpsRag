# Deployment

## Prerequisites

| Requirement | How to verify |
|---|---|
| Azure CLI, logged in | `az account show` |
| VS Professional subscription selected | `az account show --query name` |
| **Spending limit enabled** | Portal → Cost Management → Budgets |
| Azure OpenAI resource with `gpt-4o-mini` and `text-embedding-3-small` | `az cognitiveservices account deployment list -n <name> -g <rg>` |
| Docker (for local image builds) | `docker version` |
| Node 22 (for local UI builds) | `node -v` |

> **Before you start:** confirm the subscription spending limit is enabled. This
> project targets **≤ $20/month** and never upgrades a free tier.

## 1. Verify Azure OpenAI quota

```bash
az cognitiveservices account deployment list \
  --name <your-openai-resource> \
  --resource-group <your-rg> \
  --query "[].{name:name, model:properties.model.name, capacity:sku.capacity}" \
  --output table
```

You need both `gpt-4o-mini` and `text-embedding-3-small`. If either is missing,
create the deployment in the portal or report the blocker — do not change the
architecture.

## 2. Verify Azure AI Search Free availability

Only **one** Free search service is allowed per subscription.

```bash
az search service list --query "[?sku.name=='free'].name" --output tsv
```

If a Free service already exists, either reuse it or delete it first.

## 3. Build and push the image

CI builds and pushes to GHCR on every push to `main`:

```
ghcr.io/thebestcdr/itopsrag:<sha>
ghcr.io/thebestcdr/itopsrag:latest
```

To build locally:

```bash
docker build -t itopsrag:local .
docker run --rm -p 8000:8000 \
  -e DATA_DIR=/app/data \
  -v "$PWD/agent-eval-part-i-grounded-rag-benchmark:/app/data:ro" \
  itopsrag:local
```

## 4. Provision infrastructure

```bash
az login
az account set --subscription "<VS Professional subscription id>"

export AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
export API_KEY="$(openssl rand -hex 24)"     # protects /ask and /search
export LANGSMITH_API_KEY=""                  # optional

bash infra/deploy.sh
```

The script prints the app URL and the search endpoint. It creates:

- Azure AI Search (**Free**)
- Container Apps environment + app (`minReplicas=0`, `maxReplicas=2`)
- Log Analytics workspace with a **1 GB/day cap**
- Application Insights
- Budget alerts at $25 and $40
- A system-assigned managed identity with `Search Index Data Reader`

## 5. Grant the app access to Azure OpenAI

The container app's managed identity needs the `Cognitive Services OpenAI User`
role on the Azure OpenAI resource:

```bash
PRINCIPAL_ID=$(az deployment group show -g rg-itops-rag -n main \
  --query properties.outputs.appPrincipalId.value -o tsv)

az role assignment create \
  --assignee "$PRINCIPAL_ID" \
  --role "Cognitive Services OpenAI User" \
  --scope $(az cognitiveservices account show -n <openai-resource> -g <rg> --query id -o tsv)
```

## 6. Index the corpus

The corpus is not in the image (it is never committed). Run the indexer from a
machine that has the data and Azure credentials:

```bash
export DATA_DIR=/path/to/agent-eval-part-i-grounded-rag-benchmark
export AZURE_SEARCH_ENDPOINT=https://srch-itops-<suffix>.search.windows.net
export AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
export AZURE_EMBEDDING_DIMENSIONS=512

python -m itops_rag.index --recreate
```

Expected output: `Indexed 3600 chunks into 'itops-chunks'.`

Re-running without `--recreate` is safe and idempotent.

## 7. Verify

```bash
URL=$(az deployment group show -g rg-itops-rag -n main \
  --query properties.outputs.appUrl.value -o tsv)

curl -s "$URL/healthz"
curl -s "$URL/readyz"

curl -s -X POST "$URL/ask" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $API_KEY" \
  -d '{"question":"What is the acknowledge and mitigation target for Severity 4 incidents?"}'
```

The first request after idle triggers a cold start (allow ~10–20 s).

## 8. CI/CD with OIDC

The deploy workflow uses federated credentials, not stored secrets.

1. Create an app registration and federated credential for this repo:

```bash
az ad app create --display-name itops-rag-github
APP_ID=$(az ad app list --display-name itops-rag-github --query "[0].appId" -o tsv)

az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:TheBestCdr/ITOpsRag:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'

az ad sp create --id "$APP_ID"
```

2. Grant it Contributor on the resource group:

```bash
az role assignment create --assignee "$APP_ID" --role Contributor \
  --scope $(az group show -n rg-itops-rag --query id -o tsv)
```

3. Add these repository secrets:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | `$APP_ID` |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |
| `AZURE_RESOURCE_GROUP` | `rg-itops-rag` |
| `NAME_SUFFIX` | the suffix used at provisioning |
| `AZURE_OPENAI_ENDPOINT` | your endpoint |
| `APP_API_KEY` | the API key you set |
| `LANGSMITH_API_KEY` | optional |

## 9. Monitoring

- **Application Insights** — request rates, latency, failures, dependency calls.
- **Log Analytics** — structured JSON logs, queryable by `request_id`:

```kusto
traces
| where message == "ask"
| project timestamp, request_id = customDimensions.request_id,
          latency_ms = customDimensions.latency_ms,
          citations = customDimensions.citations
| order by timestamp desc
| take 50
```

- **LangSmith** — set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` to trace
  retrieval and generation spans.

## 10. Teardown

```bash
bash infra/teardown.sh
```

Deletes the resource group and everything in it. Nothing outside the group is
touched.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/readyz` returns 500 | `DATA_DIR` missing or corpus incomplete | Mount the data or set `DATA_DIR` |
| `/ask` returns 503 | `KILL_SWITCH=true` | Set `KILL_SWITCH=false` |
| `/ask` returns 401 | `API_KEY` set but header missing | Send `X-API-Key` |
| `/ask` returns 429 | Rate limit or daily cap | Wait, or raise the limits |
| Answers always refuse | Index empty or embeddings mismatched | Re-run the indexer; check dimensions |
| Cold start > 30 s | Image too large | Confirm torch is not installed in the image |
