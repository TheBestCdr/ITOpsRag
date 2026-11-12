# Teardown

## Full teardown

```bash
bash infra/teardown.sh
```

This deletes the resource group `rg-itops-rag` and everything inside it:

- Azure AI Search service (Free)
- Container Apps app and environment
- Log Analytics workspace
- Application Insights
- Budget alerts

Nothing outside the resource group is affected.

## Verify

```bash
az group list --query "[?name=='rg-itops-rag']" --output table
```

An empty result means teardown completed.

## Partial teardown

### Stop the app but keep the index

Set the kill switch, which makes `/ask` and `/search` return 503 while leaving
health probes working:

```bash
az containerapp update -n ca-itops-<suffix> -g rg-itops-rag \
  --set-env-vars KILL_SWITCH=true
```

### Scale to zero permanently

Already the default (`minReplicas=0`). To be explicit:

```bash
az containerapp update -n ca-itops-<suffix> -g rg-itops-rag \
  --min-replicas 0 --max-replicas 0
```

### Delete only the search index

```bash
az rest --method delete \
  --url "https://srch-itops-<suffix>.search.windows.net/indexes/itops-chunks?api-version=2024-07-01" \
  --resource "https://search.azure.com"
```

Rebuild with `python -m itops_rag.index --recreate`.

## Rebuild after teardown

The whole stack is reproducible from code:

```bash
export AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
bash infra/deploy.sh
python -m itops_rag.index --recreate
```

## Notes

- **Azure AI Search Free** may be deleted by Azure after extended inactivity.
  Recovery is one command (`python -m itops_rag.index --recreate`), because
  indexing is idempotent and the corpus is the source of truth.
- **GHCR images** are not deleted by teardown. Remove them from the repository's
  Packages page if you want a clean slate.
- **The Azure OpenAI resource is not created by this project.** It is an existing
  resource you supply, so teardown does not touch it.
