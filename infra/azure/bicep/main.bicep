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

// --- Nomes dos recursos com sufixo único ---
var pgServerName = '${baseName}-pg-${uniqueString(resourceGroup().id)}'
var pgDbName = 'whatsapp_assistant'
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
// 2. Redis — self-hosted (Container App), NÃO mais Azure Cache gerenciado.
// O Azure Cache for Redis Basic C0 foi REMOVIDO (FinOps: ~R$32/mês ocioso).
// O Redis passou a ser um Container App self-hosted, e o REDIS_URL é definido
// diretamente nas apps (deploy_app.sh / make ship). A regra de autoescala KEDA
// (--scale-rule-auth "address=redis-url") precisa de um secret 'redis-url'
// apontando para esse Redis self-hosted.
// ============================================

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

// Connection string do PostgreSQL para o Key Vault
var pgConnectionString = 'postgresql+asyncpg://${pgAdminLogin}:${pgAdminPassword}@${pgServer.properties.fullyQualifiedDomainName}:5432/${pgDbName}?ssl=require'

resource secretDbUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'DATABASE-URL'
  properties: { value: pgConnectionString }
}

// Redis URL: NÃO é mais provisionado aqui (Azure Cache C0 removido). O Redis é um
// Container App self-hosted; defina o secret 'REDIS-URL'/env REDIS_URL apontando
// para ele no deploy das apps (deploy_app.sh / make ship).

// ============================================
// Outputs
// ============================================
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output keyVaultUri string = keyVault.properties.vaultUri
output pgServerFqdn string = pgServer.properties.fullyQualifiedDomainName
