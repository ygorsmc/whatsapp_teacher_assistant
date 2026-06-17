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

# IMPORTANTE: rode `make env-prod` antes (copia .env.prod -> .env). Para um
# provisionamento fiel de produção o .env precisa estar PREENCHIDO — secrets
# vazios são pulados (resources condicionais) e o app sobe sem essa credencial.
if [ ! -f .env ]; then
  echo "❌ Arquivo .env não encontrado. Rode 'make env-prod' (copia .env.prod) antes." >&2
  exit 1
fi

echo "🔑 Extraindo segredos/config do seu arquivo .env..."
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
    googleApiKey="$GOOGLE_API_KEY" \
    qdrantApiKey="$QDRANT_API_KEY" \
    qdrantUrl="$QDRANT_URL" \
    azureOpenAiApiKey="$AZURE_OPENAI_API_KEY" \
    azureOpenAiEndpoint="$AZURE_OPENAI_ENDPOINT" \
    documentIntelligenceKey="$DOCUMENT_INTELLIGENCE_KEY" \
    documentIntelligenceEndpoint="$DOCUMENT_INTELLIGENCE_ENDPOINT" \
    telegramBotToken="$TELEGRAM_BOT_TOKEN" \
    telegramWebhookSecret="$TELEGRAM_WEBHOOK_SECRET" \
    phoneNumberId="$PHONE_NUMBER_ID" \
    whatsappApiUrl="${WHATSAPP_API_URL:-https://graph.facebook.com/v25.0}" \
    googleDriveFolderId="$GOOGLE_DRIVE_FOLDER_ID" \
    googleSheetId="$GOOGLE_SHEET_ID" \
    defaultLlmProvider="${DEFAULT_LLM_PROVIDER:-gemini}" \
    defaultEmbedderProvider="${DEFAULT_EMBEDDER_PROVIDER:-azure}"

echo ""
echo "✅ Infraestrutura Azure criada com sucesso!"
echo "   Credenciais do PostgreSQL armazenadas no Key Vault."
echo "   Senha do PostgreSQL gerada: $PG_PASSWORD"
echo "   (Salve-a agora, pois não será exibida novamente)"
