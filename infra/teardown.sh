#!/usr/bin/env bash
# Tear down everything this project created.
#
# Deleting the resource group removes the search service, container app,
# Log Analytics workspace, App Insights, and the budget. Nothing else in the
# subscription is touched.

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-itops-rag}"

echo "This will DELETE resource group: $RESOURCE_GROUP"
read -r -p "Type the resource group name to confirm: " CONFIRM
if [[ "$CONFIRM" != "$RESOURCE_GROUP" ]]; then
  echo "Aborted."
  exit 1
fi

az group delete --name "$RESOURCE_GROUP" --yes --no-wait
echo "Deletion started. Verify with: az group list --query \"[?name=='$RESOURCE_GROUP']\""
