#!/usr/bin/env bash
# Provision the IT Operations RAG assistant.
#
# Prerequisites:
#   az login
#   az account set --subscription <VS Professional subscription id>
#   Confirm the subscription spending limit is ENABLED before running this.
#
# This script never upgrades a free tier and never removes a spending limit.

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-itops-rag}"
LOCATION="${LOCATION:-eastus}"
NAME_SUFFIX="${NAME_SUFFIX:-$(openssl rand -hex 3)}"
IMAGE="${IMAGE:-ghcr.io/thebestcdr/itopsrag:latest}"
OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:?set AZURE_OPENAI_ENDPOINT}"
API_KEY="${API_KEY:-}"
LANGSMITH_API_KEY="${LANGSMITH_API_KEY:-}"

echo "Resource group : $RESOURCE_GROUP"
echo "Location       : $LOCATION"
echo "Name suffix    : $NAME_SUFFIX"
echo "Image          : $IMAGE"
echo

read -r -p "Create/update these resources? (yes/no) " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
  echo "Aborted."
  exit 1
fi

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters \
      nameSuffix="$NAME_SUFFIX" \
      containerImage="$IMAGE" \
      azureOpenAiEndpoint="$OPENAI_ENDPOINT" \
      apiKey="$API_KEY" \
      langsmithApiKey="$LANGSMITH_API_KEY" \
  --output json > /tmp/itops-deploy.json

echo
echo "Deployment outputs:"
az deployment group show \
  --resource-group "$RESOURCE_GROUP" \
  --name main \
  --query properties.outputs \
  --output json

echo
echo "Next steps:"
echo "  1. Grant the app identity 'Cognitive Services OpenAI User' on the Azure OpenAI resource."
echo "  2. Run the indexer:  python -m itops_rag.index --recreate"
echo "  3. Open the app URL printed above."
