<#
.SYNOPSIS
    Provision and deploy the IT Operations RAG assistant to Azure.

.DESCRIPTION
    Run this from Windows PowerShell where you are already authenticated with
    `az login` using your Visual Studio Professional subscription.

    This script:
      1. Verifies the Azure session and subscription
      2. Checks Azure OpenAI deployments and Free AI Search availability
      3. Creates the resource group
      4. Deploys the Bicep infrastructure
      5. Grants the app identity access to Azure OpenAI
      6. Prints the app URL and next steps

    It never upgrades a free tier and never removes a spending limit.

.EXAMPLE
    .\infra\deploy.ps1 -AzureOpenAiEndpoint "https://my-resource.openai.azure.com"

.EXAMPLE
    .\infra\deploy.ps1 -AzureOpenAiEndpoint "https://my-resource.openai.azure.com" `
        -RegistryUsername "TheBestCdr" -RegistryPassword $env:GHCR_TOKEN `
        -ApiKey "my-secret-key"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AzureOpenAiEndpoint,

    [string]$ResourceGroup = "rg-itops-rag",
    [string]$Location = "eastus",
    [string]$NameSuffix = "",
    [string]$Image = "ghcr.io/thebestcdr/itopsrag:latest",
    [string]$ChatDeployment = "gpt-5-mini",
    [string]$EmbeddingDeployment = "text-embedding-3-small",
    [int]$EmbeddingDimensions = 512,

    # GHCR packages are private by default, so Container Apps needs credentials.
    [string]$RegistryUsername = "",
    [string]$RegistryPassword = "",

    [string]$ApiKey = "",
    [string]$LangSmithApiKey = "",

    [string]$OpenAiResourceGroup = "",
    [string]$OpenAiResourceName = "",

    [switch]$SkipChecks,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok($Message) {
    Write-Host "    OK  $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "    !!  $Message" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 1. session

Write-Step "Verifying Azure session"

$account = az account show -o json 2>$null | ConvertFrom-Json
if (-not $account) {
    throw "Not logged in. Run 'az login' in this PowerShell window first."
}

Write-Ok "Subscription : $($account.name)"
Write-Ok "Subscription ID: $($account.id)"
Write-Ok "Tenant       : $($account.tenantId)"
Write-Ok "User         : $($account.user.name)"

if ($account.name -notmatch "Visual Studio") {
    Write-Warn "Subscription name does not contain 'Visual Studio'. Confirm this is the intended subscription."
}

# ---------------------------------------------------------------- 2. checks

if (-not $SkipChecks) {
    Write-Step "Checking Azure OpenAI deployments"

    if ($OpenAiResourceName -and $OpenAiResourceGroup) {
        $deployments = az cognitiveservices account deployment list `
            --name $OpenAiResourceName `
            --resource-group $OpenAiResourceGroup `
            -o json 2>$null | ConvertFrom-Json

        if (-not $deployments) {
            Write-Warn "Could not list deployments. Verify the resource name and group."
        }
        else {
            $names = $deployments | ForEach-Object { $_.name }
            Write-Ok "Deployments found: $($names -join ', ')"

            if ($names -notcontains $ChatDeployment) {
                Write-Warn "Chat deployment '$ChatDeployment' not found. Create it before deploying."
            }
            if ($names -notcontains $EmbeddingDeployment) {
                Write-Warn "Embedding deployment '$EmbeddingDeployment' not found. Create it before deploying."
            }
        }
    }
    else {
        Write-Warn "Skipping deployment check (pass -OpenAiResourceName and -OpenAiResourceGroup to enable)."
    }

    Write-Step "Checking Azure AI Search Free availability"

    # `az search service list` requires --resource-group on current CLI versions,
    # so query the subscription-wide resource list instead. Exclude our own
    # service (from a previous run) so re-deploys are not flagged.
    $ownSearch = "srch-itops-$NameSuffix"
    $freeServices = @(az resource list --resource-type "Microsoft.Search/searchServices" `
        --query "[?sku.name=='free'].name" -o tsv 2>$null) |
        Where-Object { $_ -and $_ -ne $ownSearch }
    if ($freeServices) {
        Write-Warn "A Free search service already exists: $($freeServices -join ', ')"
        Write-Warn "Only ONE Free service is allowed per subscription."
        Write-Warn "Delete it first, or the deployment will fail."
    }
    else {
        Write-Ok "No conflicting Free search service exists; '$ownSearch' can be created or updated."
    }
}

# ---------------------------------------------------------------- 3. names

if (-not $NameSuffix) {
    $NameSuffix = -join ((48..57) + (97..102) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
}
Write-Step "Using name suffix: $NameSuffix"

if (-not $RegistryUsername) {
    Write-Warn "No -RegistryUsername supplied. If the GHCR package is private, the app will fail to pull the image."
    Write-Warn "Make the package public, or pass -RegistryUsername and -RegistryPassword."
}

# ---------------------------------------------------------------- 4. deploy

Write-Step "Creating resource group '$ResourceGroup' in '$Location'"

if ($WhatIf) {
    Write-Host "    [WhatIf] az group create --name $ResourceGroup --location $Location"
}
else {
    az group create --name $ResourceGroup --location $Location -o none
    Write-Ok "Resource group ready"
}

Write-Step "Deploying Bicep infrastructure"

$deployArgs = @(
    "deployment", "group", "create",
    "--resource-group", $ResourceGroup,
    "--template-file", "infra/main.bicep",
    "--parameters",
    "nameSuffix=$NameSuffix",
    "containerImage=$Image",
    "azureOpenAiEndpoint=$AzureOpenAiEndpoint",
    "chatDeployment=$ChatDeployment",
    "embeddingDeployment=$EmbeddingDeployment",
    "embeddingDimensions=$EmbeddingDimensions",
    "apiKey=$ApiKey",
    "langsmithApiKey=$LangSmithApiKey",
    "registryUsername=$RegistryUsername",
    "registryPassword=$RegistryPassword",
    "--output", "json"
)

if ($WhatIf) {
    Write-Host "    [WhatIf] az $($deployArgs -join ' ')"
    return
}

$result = az @deployArgs 2>$null | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $result) {
    throw "Deployment failed. Check the Azure portal for details."
}

$outputs = $result.properties.outputs
$appUrl = $outputs.appUrl.value
$appName = $outputs.appName.value
$searchName = $outputs.searchName.value
$searchEndpoint = $outputs.searchEndpoint.value
$principalId = $outputs.appPrincipalId.value

Write-Ok "App URL        : $appUrl"
Write-Ok "App name       : $appName"
Write-Ok "Search service : $searchName"
Write-Ok "Search endpoint: $searchEndpoint"
Write-Ok "App identity   : $principalId"

# ---------------------------------------------------------------- 5. RBAC

if ($OpenAiResourceName -and $OpenAiResourceGroup) {
    Write-Step "Granting the app identity access to Azure OpenAI"

    $openAiId = az cognitiveservices account show `
        --name $OpenAiResourceName `
        --resource-group $OpenAiResourceGroup `
        --query id -o tsv

    az role assignment create `
        --assignee $principalId `
        --role "Cognitive Services OpenAI User" `
        --scope $openAiId `
        -o none 2>$null

    if ($LASTEXITCODE -eq 0) {
        Write-Ok "Granted 'Cognitive Services OpenAI User'"
    }
    else {
        Write-Warn "Role assignment may already exist, or you lack permission. Verify in the portal."
    }
}
else {
    Write-Warn "Skipped Azure OpenAI role assignment (pass -OpenAiResourceName and -OpenAiResourceGroup)."
}

# ---------------------------------------------------------------- 6. summary

Write-Step "Deployment complete"

Write-Host ""
Write-Host "  App URL        : $appUrl" -ForegroundColor White
Write-Host "  Resource group : $ResourceGroup" -ForegroundColor White
Write-Host "  Search service : $searchName" -ForegroundColor White
Write-Host "  Name suffix    : $NameSuffix" -ForegroundColor White
Write-Host ""
Write-Host "  Next: index the corpus (run from the repo root):" -ForegroundColor Yellow
Write-Host ""
Write-Host "    `$env:DATA_DIR = 'C:\ProjPlayGround\ICS_DL\Caproj\agent-eval-part-i-grounded-rag-benchmark'" -ForegroundColor Gray
Write-Host "    `$env:AZURE_SEARCH_ENDPOINT = '$searchEndpoint'" -ForegroundColor Gray
Write-Host "    `$env:AZURE_OPENAI_ENDPOINT = '$AzureOpenAiEndpoint'" -ForegroundColor Gray
Write-Host "    `$env:AZURE_EMBEDDING_DIMENSIONS = '$EmbeddingDimensions'" -ForegroundColor Gray
Write-Host "    python -m itops_rag.index --recreate" -ForegroundColor Gray
Write-Host ""
Write-Host "  Then verify:" -ForegroundColor Yellow
Write-Host "    curl.exe `"$appUrl/readyz`"" -ForegroundColor Gray
Write-Host ""
