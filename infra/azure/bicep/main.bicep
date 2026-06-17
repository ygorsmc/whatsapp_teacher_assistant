@description('Nome base para os recursos')
param baseName string = 'whatsapp-bot'

@description('Região do Azure')
param location string = 'brazilsouth'

@description('Login do administrador do banco PostgreSQL')
param pgAdminLogin string = 'pgadmin'

@secure()
@description('Senha do administrador do banco PostgreSQL')
param pgAdminPassword string

@secure()
@description('Token do WhatsApp')
param whatsappToken string

@secure()
@description('Token de Verificação do Meta')
param metaVerifyToken string

@secure()
@description('App Secret do Meta (usado para validar assinaturas)')
param metaAppSecret string

@secure()
@description('Chave da API do Google Gemini')
param googleApiKey string

// Segredos do app que hoje vivem como env em CLARO no Container App (achado
// F02). Default vazio + criação condicional → não quebra deploys que ainda não
// passam o param; quando informados (valores ROTACIONADOS), entram no Key Vault
// e o app os lê via Managed Identity (azure_kv.load_azure_secrets).
@secure()
@description('Qdrant Cloud API key')
param qdrantApiKey string = ''

@secure()
@description('Azure OpenAI API key')
param azureOpenAiApiKey string = ''

@secure()
@description('Azure Document Intelligence key')
param documentIntelligenceKey string = ''

@secure()
@description('Telegram Bot token')
param telegramBotToken string = ''

@secure()
@description('Telegram webhook secret token')
param telegramWebhookSecret string = ''

// Endpoints semi-sensíveis que o app busca DO KEY VAULT (azure_kv.load_azure_secrets
// faz fetch de QDRANT-URL e AZURE-OPENAI-ENDPOINT). Mantê-los aqui evita env em
// claro no Container App e garante reconstrução determinística.
@secure()
@description('Qdrant Cloud URL (endpoint do cluster)')
param qdrantUrl string = ''

@secure()
@description('Azure OpenAI endpoint')
param azureOpenAiEndpoint string = ''

// Config NÃO-sensível que o loader NÃO busca do KV → entra como env de container.
@description('Azure Document Intelligence endpoint (PARSER_BACKEND=azure)')
param documentIntelligenceEndpoint string = ''

@description('Meta Phone Number ID (WhatsApp)')
param phoneNumberId string = ''

@description('URL base da Graph API do WhatsApp')
param whatsappApiUrl string = 'https://graph.facebook.com/v25.0'

@description('ID da pasta raiz do Google Drive (materiais)')
param googleDriveFolderId string = ''

@description('ID da planilha do Google Sheets (registro)')
param googleSheetId string = ''

@description('Provider de LLM padrão (get_provider)')
param defaultLlmProvider string = 'gemini'

@description('Provider de embeddings padrão (get_embedder)')
param defaultEmbedderProvider string = 'azure'

// --- Nomes dos recursos com sufixo único ---
var pgServerName = '${baseName}-pg-${uniqueString(resourceGroup().id)}'
var pgDbName = 'whatsapp_assistant'
var redisName = '${baseName}-redis-${uniqueString(resourceGroup().id)}'
var keyVaultName = 'kv-wa-${substring(uniqueString(resourceGroup().id), 0, 10)}'
var registryName = replace('${baseName}acr${uniqueString(resourceGroup().id)}', '-', '')

// ============================================
// 1. Azure Database for PostgreSQL Flexible Server (Burstable B1ms — FinOps)
// ============================================
resource pgServer 'Microsoft.DBforPostgreSQL/flexibleServers@2022-12-01' = {
  name: pgServerName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: pgAdminLogin
    administratorLoginPassword: pgAdminPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2022-12-01' = {
  parent: pgServer
  name: pgDbName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Firewall: permite acesso de serviços Azure (Container Apps, Functions, etc.)
resource pgFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2022-12-01' = {
  parent: pgServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// ============================================
// 2. Azure Cache for Redis (Basic C0 — FinOps)
// ============================================
resource redis 'Microsoft.Cache/redis@2023-08-01' = {
  name: redisName
  location: location
  properties: {
    sku: {
      name: 'Basic'
      family: 'C'
      capacity: 0 // C0 = 250 MB, suficiente para filas + cache
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: {
      'maxmemory-policy': 'noeviction'
    }
  }
}

// ============================================
// 3. Azure Container Registry (ACR Basic)
// ============================================
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
  }
}

// ============================================
// 4. Azure Key Vault
// ============================================
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    accessPolicies: []
    enableRbacAuthorization: true
  }
}

// ============================================
// 5. Secrets no Key Vault
// ============================================
resource secretWhatsApp 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'WHATSAPP-TOKEN'
  properties: { value: whatsappToken }
}

resource secretMetaVerify 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'META-VERIFY-TOKEN'
  properties: { value: metaVerifyToken }
}

resource secretMetaApp 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'META-APP-SECRET'
  properties: { value: metaAppSecret }
}

resource secretGoogleApi 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'GOOGLE-API-KEY'
  properties: { value: googleApiKey }
}

// Secrets adicionais (F02) — criados só quando o param é informado. Os NOMES
// batem com `azure_kv.load_azure_secrets` (hífen → underscore no settings).
resource secretQdrantApi 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(qdrantApiKey)) {
  parent: keyVault
  name: 'QDRANT-API-KEY'
  properties: { value: qdrantApiKey }
}

resource secretAzureOpenAi 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(azureOpenAiApiKey)) {
  parent: keyVault
  name: 'AZURE-OPENAI-API-KEY'
  properties: { value: azureOpenAiApiKey }
}

resource secretDocIntel 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(documentIntelligenceKey)) {
  parent: keyVault
  name: 'DOCUMENT-INTELLIGENCE-KEY'
  properties: { value: documentIntelligenceKey }
}

resource secretTelegramBot 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(telegramBotToken)) {
  parent: keyVault
  name: 'TELEGRAM-BOT-TOKEN'
  properties: { value: telegramBotToken }
}

resource secretTelegramWebhook 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(telegramWebhookSecret)) {
  parent: keyVault
  name: 'TELEGRAM-WEBHOOK-SECRET'
  properties: { value: telegramWebhookSecret }
}

// Endpoints buscados por azure_kv.load_azure_secrets (QDRANT-URL / AZURE-OPENAI-ENDPOINT).
resource secretQdrantUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(qdrantUrl)) {
  parent: keyVault
  name: 'QDRANT-URL'
  properties: { value: qdrantUrl }
}

resource secretAzureOpenAiEndpoint 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(azureOpenAiEndpoint)) {
  parent: keyVault
  name: 'AZURE-OPENAI-ENDPOINT'
  properties: { value: azureOpenAiEndpoint }
}

// Connection string do PostgreSQL para o Key Vault
var pgConnectionString = 'postgresql+asyncpg://${pgAdminLogin}:${pgAdminPassword}@${pgServer.properties.fullyQualifiedDomainName}:5432/${pgDbName}?ssl=require'

resource secretDbUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'DATABASE-URL'
  properties: { value: pgConnectionString }
}

// Redis connection string (rediss:// = TLS, porta SSL 6380).
// IMPORTANTE: o username 'default' é obrigatório — o Azure Cache rejeita o AUTH
// sem username (WRONGPASS). E o /0 (db index) evita normalização inconsistente.
var redisKey = redis.listKeys().primaryKey
var redisConnectionString = '${redis.properties.hostName}:${redis.properties.sslPort}'

resource secretRedisUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'REDIS-URL'
  properties: { value: 'rediss://default:${redisKey}@${redisConnectionString}/0' }
}

// ============================================
// Outputs
// ============================================
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output keyVaultUri string = keyVault.properties.vaultUri
output pgServerFqdn string = pgServer.properties.fullyQualifiedDomainName
output redisHostname string = redis.properties.hostName

// ============================================
// 6. Log Analytics Workspace
// ============================================
var logAnalyticsName = '${baseName}-law-${uniqueString(resourceGroup().id)}'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ============================================
// 7. Container Apps Environment
// ============================================
var environmentName = 'env-${baseName}'

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ============================================
// 8. Container Apps (Webhook & Workers)
// ============================================

// Papel "Key Vault Secrets User" para acessar o Key Vault
var keyVaultSecretsUserRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

// Env NÃO-sensível compartilhado entre os apps. Os SECRETS (tokens/keys/endpoints
// sensíveis) NÃO entram aqui — vêm do Key Vault via Managed Identity em runtime
// (azure_kv.load_azure_secrets). Centralizado para que os 3 apps fiquem consistentes.
var baseEnv = [
  { name: 'AZURE_KEY_VAULT_URL', value: keyVault.properties.vaultUri }
  { name: 'ENVIRONMENT', value: 'production' }
  { name: 'QUEUE_BACKEND', value: 'redis' }
]
var messagingEnv = [
  { name: 'PHONE_NUMBER_ID', value: phoneNumberId }
  { name: 'WHATSAPP_API_URL', value: whatsappApiUrl }
]
// Config que só os Workers precisam (parsing, providers, fontes de material).
var workerConfigEnv = [
  { name: 'PARSER_BACKEND', value: 'azure' }
  { name: 'DEFAULT_LLM_PROVIDER', value: defaultLlmProvider }
  { name: 'DEFAULT_EMBEDDER_PROVIDER', value: defaultEmbedderProvider }
  { name: 'GOOGLE_DRIVE_FOLDER_ID', value: googleDriveFolderId }
  { name: 'GOOGLE_SHEET_ID', value: googleSheetId }
  { name: 'DOCUMENT_INTELLIGENCE_ENDPOINT', value: documentIntelligenceEndpoint }
]

// Container App: Webhook
resource webhookApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'webhook-app'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 80
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'webhook'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' // Imagem dummy para deploy inicial
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          // Webhook só valida/roteia/enfileira — não precisa de config de LLM/parsing.
          env: concat(baseEnv, messagingEnv)
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

// Container App: Worker LLM
resource workerLlmApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'worker-llm'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
        {
          name: 'redis-url'
          value: 'rediss://default:${redisKey}@${redisConnectionString}/0'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker-llm'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: concat(baseEnv, messagingEnv, workerConfigEnv, [
            {
              name: 'WORKER_TYPE'
              value: 'llm'
            }
          ])
        }
      ]
      scale: {
        // Worker assíncrono à fila: pode escalar a ZERO (FinOps) sem violar o SLA
        // de 200ms — quem segura o ack do webhook é o webhook-app (minReplicas=1).
        // Alinhado ao estado de produção (ver what-if): min 0 / max 10.
        minReplicas: 0
        maxReplicas: 10
        rules: [
          {
            name: 'inbound-messages-rule'
            custom: {
              type: 'redis-streams'
              auth: [
                {
                  secretRef: 'redis-url'
                  triggerParameter: 'address'
                }
              ]
              metadata: {
                stream: 'inbound_messages'
                consumerGroup: 'messaging_workers'
                pendingEntriesCount: '5'
              }
            }
          }
          // Comandos '/' são roteados para a fila command_messages ANTES de
          // inbound_messages (ver AGENTS.md). Sem esta regra o worker não escala
          // para drenar comandos.
          {
            name: 'command-messages-rule'
            custom: {
              type: 'redis-streams'
              auth: [
                {
                  secretRef: 'redis-url'
                  triggerParameter: 'address'
                }
              ]
              metadata: {
                stream: 'command_messages'
                consumerGroup: 'messaging_workers'
                pendingEntriesCount: '1'
              }
            }
          }
        ]
      }
    }
  }
}

// Container App: Worker Scheduler
resource workerSchedulerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'worker-scheduler'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker-scheduler'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
          }
          env: concat(baseEnv, messagingEnv, workerConfigEnv, [
            {
              name: 'WORKER_TYPE'
              value: 'scheduler'
            }
          ])
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ============================================
// 9. Role Assignments (Key Vault Secrets User)
// ============================================

resource webhookKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, webhookApp.id, keyVaultSecretsUserRoleDefinitionId)
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleDefinitionId
    principalId: webhookApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerLlmKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, workerLlmApp.id, keyVaultSecretsUserRoleDefinitionId)
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleDefinitionId
    principalId: workerLlmApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerSchedulerKvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, workerSchedulerApp.id, keyVaultSecretsUserRoleDefinitionId)
  scope: keyVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleDefinitionId
    principalId: workerSchedulerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
