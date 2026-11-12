<#
.SYNOPSIS
    Build the Azure AI Search index from the full Kaggle AgentEval corpus.

.DESCRIPTION
    Indexes all 1,200 documents / 3,600 chunks. Idempotent: re-running converges
    to the corpus contents instead of duplicating rows.

    Run from Windows PowerShell after deploy.ps1, from the repo root.

.EXAMPLE
    .\infra\index.ps1 -SearchEndpoint "https://srch-itops-abc123.search.windows.net" `
        -OpenAiEndpoint "https://my-resource.openai.azure.com" `
        -DataDir "C:\ProjPlayGround\ICS_DL\Caproj\agent-eval-part-i-grounded-rag-benchmark"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SearchEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$OpenAiEndpoint,

    [Parameter(Mandatory = $true)]
    [string]$DataDir,

    [string]$IndexName = "itops-chunks",
    [string]$EmbeddingDeployment = "text-embedding-3-small",
    [int]$EmbeddingDimensions = 512,
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- preflight

Write-Step "Checking the corpus"

$corpusPath = Join-Path $DataDir "corpus.csv"
if (-not (Test-Path $corpusPath)) {
    throw "corpus.csv not found at $corpusPath. Download the Kaggle AgentEval dataset first."
}

$lineCount = (Get-Content $corpusPath | Measure-Object -Line).Lines - 1
Write-Host "    corpus.csv rows (excluding header): $lineCount"
if ($lineCount -ne 3600) {
    Write-Warning "Expected 3,600 chunks but found $lineCount. The indexer will validate and fail if the corpus is incomplete."
}

Write-Step "Checking Azure session"

$account = az account show -o json 2>$null | ConvertFrom-Json
if (-not $account) {
    throw "Not logged in. Run 'az login' in this PowerShell window first."
}
Write-Host "    Subscription: $($account.name)"

# ---------------------------------------------------------------- env

Write-Step "Configuring environment for the indexer"

$env:DATA_DIR = $DataDir
$env:AZURE_SEARCH_ENDPOINT = $SearchEndpoint
$env:AZURE_SEARCH_INDEX = $IndexName
$env:AZURE_OPENAI_ENDPOINT = $OpenAiEndpoint
$env:AZURE_EMBEDDING_DEPLOYMENT = $EmbeddingDeployment
$env:AZURE_EMBEDDING_DIMENSIONS = "$EmbeddingDimensions"
$env:RETRIEVER_BACKEND = "azure"
$env:EMBEDDING_BACKEND = "azure"

Write-Host "    DATA_DIR                = $DataDir"
Write-Host "    AZURE_SEARCH_ENDPOINT   = $SearchEndpoint"
Write-Host "    AZURE_SEARCH_INDEX      = $IndexName"
Write-Host "    AZURE_OPENAI_ENDPOINT   = $OpenAiEndpoint"
Write-Host "    AZURE_EMBEDDING_DIMENSIONS = $EmbeddingDimensions"

# ---------------------------------------------------------------- run

Write-Step "Indexing the corpus"

$indexArgs = @("-m", "itops_rag.index")
if ($Recreate) {
    $indexArgs += "--recreate"
    Write-Host "    Recreating the index schema first."
}

python @indexArgs
if ($LASTEXITCODE -ne 0) {
    throw "Indexing failed. Check the error above."
}

# ---------------------------------------------------------------- verify

Write-Step "Verifying the index"

$count = az rest --method get `
    --url "$SearchEndpoint/indexes/$IndexName/docs/`$count?api-version=2024-07-01" `
    --resource "https://search.azure.com" `
    --query "count" -o tsv 2>$null

if ($count) {
    Write-Host "    Documents in index: $count" -ForegroundColor Green
    if ([int]$count -ne 3600) {
        Write-Warning "Expected 3,600 documents. Re-run with -Recreate if the index looks stale."
    }
}
else {
    Write-Warning "Could not read the document count. Verify in the Azure portal."
}

Write-Host ""
Write-Host "Indexing complete." -ForegroundColor Green
Write-Host ""
