# Deployment

## Prerequisites

| Requirement | How to verify |
|---|---|
| Azure CLI, logged in | `az account show -o table` |
| VS Professional subscription selected | `az account show --query name` |
| **Spending limit enabled** | Portal → Cost Management → Budgets |
| Azure OpenAI resource with `gpt-4o-mini` and `text-embedding-3-small` | `az cognitiveservices account deployment list -n <name> -g <rg>` |
| Python 3.11+ with the package installed | `python -c "import itops_rag"` |

> **Before you start:** confirm the subscription spending limit is enabled. This
> project targets **≤ $20/month** and never upgrades a free tier.

## Windows PowerShell (recommended)

The deployment scripts are provided in PowerShell because that is where the
Azure session is authenticated.

```powershell
cd C:\ProjPlayGround\ICS_DL\Caproj\itopsrag

# Confirm the session
az account show -o table

# Deploy
.\infra\deploy.ps1 `
    -AzureOpenAiEndpoint "https://<your-resource>.openai.azure.com" `
    -OpenAiResourceName "<your-openai-resource>" `
    -OpenAiResourceGroup "<your-openai-rg>" `
    -ApiKey "<a-strong-random-key>"
```

The script verifies the session, checks Azure OpenAI deployments and Free AI
Search availability, deploys the Bicep template, grants the app identity
`Cognitive Services OpenAI User`, and prints the app URL.

Use `-WhatIf` to preview the commands without changing anything, or
`-SkipChecks` to bypass the preflight checks.

Then index the corpus:

```powershell
.\infra\index.ps1 `
    -SearchEndpoint "https://srch-itops-<suffix>.search.windows.net" `
    -OpenAiEndpoint "https://<your-resource>.openai.azure.com" `
    -DataDir "C:\ProjPlayGround\ICS_DL\Caproj\agent-eval-part-i-grounded-rag-benchmark" `
    -Recreate
```

Expected: `Indexed 3600 chunks into 'itops-chunks'.`

## Bash (Linux / macOS / WSL)

```bash
az login
az account set --subscription "<VS Professional subscription id>"

export AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
export API_KEY="$(openssl rand -hex 24)"

bash infra/deploy.sh
python -m itops_rag.index --recreate
```

## Container image

CI publishes the image to GHCR on every branch push:

```
ghcr.io/thebestcdr/itopsrag:<full-sha>
ghcr.io/thebestcdr/itopsrag:latest
```

The package is **public**, so Container Apps pulls it anonymously. If you make
it private, pass `-RegistryUsername` and `-RegistryPassword` (a PAT with
`read:packages`) to `deploy.ps1`; the Bicep template then attaches registry
credentials.

## What gets created

| Resource | Name | Tier |
|---|---|---|
| Resource group | `rg-itops-rag` | — |
| Azure AI Search | `srch-itops-<suffix>` | **Free** (50 MB) |
| Container Apps environment | `cae-itops-<suffix>` | Consumption |
| Container App | `ca-itops-<suffix>` | min=0, max=2 |
| Log Analytics | `log-itops-<suffix>` | 1 GB/day cap |
| Application Insights | `appi-itops-<suffix>` | — |
| Budget | `budget-itops-<suffix>` | alerts at $25 / $40 |

## Verify

```powershell
$url = az deployment group show -g rg-itops-rag -n main `
    --query properties.outputs.appUrl.value -o tsv

curl.exe "$url/healthz"
curl.exe "$url/readyz"

curl.exe -X POST "$url/ask" `
    -H "Content-Type: application/json" `
    -H "X-API-Key: <your-api-key>" `
    -d '{\"question\":\"What is the acknowledge and mitigation target for Severity 4 incidents?\"}'
```

The first request after idle triggers a cold start (allow ~10–20 s).

## CI/CD with OIDC

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

## Monitoring

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

## Teardown

```powershell
az group delete --name rg-itops-rag --yes --no-wait
```

Or on bash: `bash infra/teardown.sh`

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/readyz` returns 500 | `DATA_DIR` missing or corpus incomplete | Mount the data or set `DATA_DIR` |
| `/ask` returns 503 | `KILL_SWITCH=true` | Set `KILL_SWITCH=false` |
| `/ask` returns 401 | `API_KEY` set but header missing | Send `X-API-Key` |
| `/ask` returns 429 | Rate limit or daily cap | Wait, or raise the limits |
| Answers always refuse | Index empty or embeddings mismatched | Re-run the indexer; check dimensions |
| Container fails to start | Image pull failure | Make the GHCR package public, or pass registry credentials |
| Cold start > 30 s | Image too large | Confirm torch is not installed in the image |
