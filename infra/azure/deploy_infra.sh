#!/bin/bash
set -e
# ============================================
# Deploy de Infraestrutura — Azure (Bicep)
# ============================================
# Provisiona: PostgreSQL, Redis, ACR, Key Vault
# Uso: bash infra/azure/deploy_infra.sh

RESOURCE_GROUP="rg-whatsapp-assistant-prod"
LOCATION="brazilsouth"
BICEP_FILE="infra/azure/bicep/main.bicep"

echo "🔐 Logando no Azure..."
az login

echo "📦 Criando Resource Group em $LOCATION..."
az group create --name $RESOURCE_GROUP --location $LOCATION

echo "🔑 Extraindo segredos do seu arquivo .env local..."
export $(grep -v '^#' .env | xargs)

# Cria senha aleatória segura para o banco PostgreSQL
PG_PASSWORD=$(openssl rand -base64 16)

echo "🚀 Provisionando PostgreSQL, Redis, Key Vault e ACR via Bicep..."
echo "   Isso leva cerca de 5 a 10 minutos..."

az deployment group create \
  --resource-group $RESOURCE_GROUP \
  --template-file $BICEP_FILE \
  --parameters \
    pgAdminPassword="$PG_PASSWORD" \
    whatsappToken="$WHATSAPP_TOKEN" \
    metaVerifyToken="$META_VERIFY_TOKEN" \
    metaAppSecret="$META_APP_SECRET" \
    googleApiKey="$GOOGLE_API_KEY"

echo ""
echo "✅ Infraestrutura Azure criada com sucesso!"
echo "   Credenciais do PostgreSQL armazenadas no Key Vault."
echo "   Senha do PostgreSQL gerada: $PG_PASSWORD"
echo "   (Salve-a agora, pois não será exibida novamente)"
