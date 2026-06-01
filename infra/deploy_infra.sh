#!/bin/bash
# Script para iniciar a criação automatizada da infraestrutura no Azure (Brazil South)

RESOURCE_GROUP="rg-whatsapp-assistant-prod"
LOCATION="brazilsouth"
BICEP_FILE="infra/bicep/main.bicep"

echo "Logando no Azure..."
az login

echo "Criando Resource Group no $LOCATION..."
az group create --name $RESOURCE_GROUP --location $LOCATION

echo "Extraindo segredos do seu arquivo .env local..."
# Pega do .env local (apenas um quebra galho para o deploy automático não expor senhas no código)
export $(grep -v '^#' .env | xargs)

# Cria senha aleatória segura para o banco
SQL_PASSWORD=$(openssl rand -base64 16)

echo "Provisionando Banco de Dados, Service Bus, Key Vault e ACR via Bicep..."
echo "Isso leva cerca de 3 a 5 minutos..."

az deployment group create \
  --resource-group $RESOURCE_GROUP \
  --template-file $BICEP_FILE \
  --parameters \
    sqlAdminPassword="$SQL_PASSWORD" \
    whatsappToken="$WHATSAPP_TOKEN" \
    metaVerifyToken="$META_VERIFY_TOKEN" \
    metaAppSecret="$META_APP_SECRET" \
    googleApiKey="$GOOGLE_API_KEY" \
    azureOpenaiApiKey="$AZURE_OPENAI_API_KEY"

echo "Infraestrutura criada com sucesso!"
echo "O Bicep acabou de criar a casca E já inseriu suas chaves com segurança dentro do Azure Key Vault!"
