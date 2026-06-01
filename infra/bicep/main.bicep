@description('Nome base para os recursos')
param baseName string = 'whatsapp-bot'

@description('Região do Azure')
param location string = 'brazilsouth'

@description('Login do administrador do banco de dados')
param sqlAdminLogin string = 'sqladmin'

@secure()
@description('Senha do administrador do banco de dados')
param sqlAdminPassword string

@secure()
@description('Token do WhatsApp')
param whatsappToken string

@secure()
@description('Token de Verificação do Meta')
param metaVerifyToken string

@secure()
@description('Chave da API do Google Gemini')
param googleApiKey string

@secure()
@description('Chave da API de Embeddings da Azure')
param azureOpenaiApiKey string

var sqlServerName = '${baseName}-sqlserver-${uniqueString(resourceGroup().id)}'
var sqlDbName = '${baseName}-db'
var serviceBusNamespaceName = '${baseName}-sb-${uniqueString(resourceGroup().id)}'

// Key Vault suporta no máximo 24 caracteres!
var keyVaultName = 'kv-wa-${substring(uniqueString(resourceGroup().id), 0, 10)}'
var registryName = replace('${baseName}acr${uniqueString(resourceGroup().id)}', '-', '')

// 1. Azure SQL Server e Banco de Dados (DTU Basic - Mais Barato)
resource sqlServer 'Microsoft.Sql/servers@2023-05-01-preview' = {
  name: sqlServerName
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    publicNetworkAccess: 'Enabled'
  }
}

resource sqlFirewall 'Microsoft.Sql/servers/firewallRules@2023-05-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureIPs'
  properties: {
    startIpAddress: '0.0.0.0' // Permite serviços do Azure
    endIpAddress: '0.0.0.0'
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2023-05-01-preview' = {
  parent: sqlServer
  name: sqlDbName
  location: location
  sku: {
    name: 'Basic'
    tier: 'Basic'
    capacity: 5 // DTUs
  }
}

// 2. Service Bus (Standard Tier para Sessões FIFO)
resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusNamespaceName
  location: location
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
}

resource serviceBusQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'inbound_messages'
  properties: {
    requiresSession: true
    lockDuration: 'PT5M' // Lock de 5 minutos
    maxDeliveryCount: 10
  }
}

// Authorization Rule para pegar a Connection String
resource sbAuthRule 'Microsoft.ServiceBus/namespaces/AuthorizationRules@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'RootManageSharedAccessKey'
  properties: {
    rights: [
      'Listen'
      'Manage'
      'Send'
    ]
  }
}

// 3. Azure Container Registry (ACR)
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

// 4. Azure Key Vault
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    accessPolicies: [] // Opcional: Adicionar a identidade do ACA depois
    enableRbacAuthorization: true // Modern approach para secrets
  }
}

// 5. Inserindo os Secrets no Key Vault (Step 3 do manual via código!)
resource secretWhatsApp 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'WHATSAPP-TOKEN'
  properties: {
    value: whatsappToken
  }
}

resource secretMetaVerify 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'META-VERIFY-TOKEN'
  properties: {
    value: metaVerifyToken
  }
}

resource secretGoogleApi 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'GOOGLE-API-KEY'
  properties: {
    value: googleApiKey
  }
}

resource secretAzureOpenAi 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'AZURE-OPENAI-API-KEY'
  properties: {
    value: azureOpenaiApiKey
  }
}

// A connection string do Service Bus e do SQL precisam ser formatadas e inseridas no Key Vault
var sqlConnectionString = 'Driver={ODBC Driver 18 for SQL Server};Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Database=${sqlDbName};Uid=${sqlAdminLogin};Pwd=${sqlAdminPassword};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;'

resource secretDbUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'DATABASE-URL'
  properties: {
    value: sqlConnectionString
  }
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output keyVaultUri string = keyVault.properties.vaultUri
