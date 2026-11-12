// Infrastructure for the IT Operations RAG assistant.
//
// Cost controls baked in:
//   * Azure AI Search on the Free tier (50 MB, 3 indexes, 1 per subscription)
//   * Container Apps Consumption with minReplicas = 0 (scale to zero)
//   * Log Analytics daily cap so telemetry cannot run away
//   * Budget alerts at $25 and $40
//
// Nothing here creates a paid tier. Do not change `sku.name` on the search
// service without an explicit cost review.

targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short unique suffix for globally-unique names (e.g. "a1b2c3").')
param nameSuffix string

@description('Container image to deploy, e.g. ghcr.io/owner/repo:sha.')
param containerImage string

@description('Azure OpenAI endpoint (existing resource).')
param azureOpenAiEndpoint string

@description('Azure OpenAI chat deployment name.')
param chatDeployment string = 'gpt-5-mini'

@description('Azure OpenAI embedding deployment name.')
param embeddingDeployment string = 'text-embedding-3-small'

@description('Embedding vector dimensions. 512 keeps the index small.')
param embeddingDimensions int = 512

@description('API key required by /ask and /search. Leave empty to disable auth.')
@secure()
param apiKey string = ''

@description('LangSmith API key for tracing. Leave empty to disable.')
@secure()
param langsmithApiKey string = ''

@description('GHCR username (GitHub account) for pulling the private image.')
param registryUsername string = ''

@description('GHCR personal access token with read:packages scope.')
@secure()
param registryPassword string = ''

@description('Daily Log Analytics ingestion cap in GB.')
param logDailyCapGb int = 1

@description('Budget start date (first of the current month).')
param budgetStartDate string = '2026-11-01'

@description('Tags applied to every resource.')
param tags object = {
  project: 'itops-rag'
  environment: 'dev'
  owner: 'capstone'
}

// Container Apps rejects empty secret values, so only emit a secret (and its
// env reference) when a real value was supplied.
var hasApiKey = !empty(apiKey)
var hasLangsmith = !empty(langsmithApiKey)
var hasRegistry = !empty(registryUsername) && !empty(registryPassword)

var appSecrets = concat(
  hasApiKey ? [{ name: 'api-key', value: apiKey }] : [],
  hasLangsmith ? [{ name: 'langsmith-api-key', value: langsmithApiKey }] : [],
  hasRegistry ? [{ name: 'registry-password', value: registryPassword }] : []
)

// ---------------------------------------------------------------- search

// Free tier: 50 MB storage, 3 indexes, one free service per subscription.
// This is the binding constraint on index size, so keep vectors at 512 dims.
resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: 'srch-itops-${nameSuffix}'
  location: location
  tags: tags
  sku: {
    name: 'free'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: false
    // The app authenticates with its managed identity, so Entra ID (AAD) auth
    // must be enabled. Without this the service is apiKeyOnly and the app's
    // DefaultAzureCredential is rejected with 403 Forbidden.
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// ---------------------------------------------------------------- monitoring

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-itops-${nameSuffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    // Hard daily cap: telemetry can never exceed this, which protects the budget.
    workspaceCapping: {
      dailyQuotaGb: logDailyCapGb
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-itops-${nameSuffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ---------------------------------------------------------------- container apps

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-itops-${nameSuffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-itops-${nameSuffix}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      // Only attach registry credentials when supplied. The GHCR package for
      // this project is public, so anonymous pulls work without them.
      registries: hasRegistry ? [
        {
          server: 'ghcr.io'
          username: registryUsername
          passwordSecretRef: 'registry-password'
        }
      ] : null
      secrets: appSecrets
    }
    template: {
      // Scale to zero: no cost when idle.
      scale: {
        minReplicas: 0
        maxReplicas: 2
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
      containers: [
        {
          name: 'itops-rag'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(
            [
              { name: 'ENVIRONMENT', value: 'azure' }
              { name: 'RETRIEVER_BACKEND', value: 'azure' }
              { name: 'EMBEDDING_BACKEND', value: 'azure' }
              { name: 'GENERATOR_BACKEND', value: 'azure_openai' }
              { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAiEndpoint }
              { name: 'AZURE_CHAT_DEPLOYMENT', value: chatDeployment }
              { name: 'AZURE_EMBEDDING_DEPLOYMENT', value: embeddingDeployment }
              { name: 'AZURE_EMBEDDING_DIMENSIONS', value: string(embeddingDimensions) }
              { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
              { name: 'AZURE_SEARCH_INDEX', value: 'itops-chunks' }
              { name: 'DATA_DIR', value: '/app/data' }
              { name: 'WEB_DIST_DIR', value: '/app/web/dist' }
              { name: 'RATE_LIMIT_PER_MINUTE', value: '30' }
              { name: 'DAILY_REQUEST_CAP', value: '2000' }
              { name: 'MAX_OUTPUT_TOKENS', value: '400' }
              { name: 'LANGSMITH_TRACING', value: hasLangsmith ? 'true' : 'false' }
              { name: 'LANGSMITH_PROJECT', value: 'itops-rag' }
              { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
            ],
            hasApiKey ? [{ name: 'API_KEY', secretRef: 'api-key' }] : [],
            hasLangsmith ? [{ name: 'LANGSMITH_API_KEY', secretRef: 'langsmith-api-key' }] : []
          )
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/readyz'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 20
            }
          ]
        }
      ]
    }
  }
}

// ---------------------------------------------------------------- RBAC

// The app reads the search index and calls Azure OpenAI with its managed identity.
resource searchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, app.id, 'search-index-data-reader')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '1407120a-92aa-4202-b7e9-c0e197c71c8f'
    )
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- budget

// Budget alerts only fire; they do not stop spending. The subscription
// spending limit is the actual hard stop and must stay enabled.
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-itops-${nameSuffix}'
  properties: {
    category: 'Cost'
    amount: 20
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      warning: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 125
        contactRoles: [
          'Owner'
        ]
      }
      critical: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 200
        contactRoles: [
          'Owner'
        ]
      }
    }
  }
}

// ---------------------------------------------------------------- outputs

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output appName string = app.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchName string = search.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output appPrincipalId string = app.identity.principalId
