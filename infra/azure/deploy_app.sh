#!/bin/bash
set -e
# Script para fazer deploy do código direto para o Azure (Bypass do bloqueio da Faculdade)

RESOURCE_GROUP="rg-whatsapp-assistant-prod"
ACR_NAME="whatsappbotacrbdn4p6wsfyc3i" # Nome do seu ACR retornado pelo Bicep

echo "🚀 Iniciando Deploy para a Nuvem..."

echo "📦 1. Enviando o código para o Azure construir a imagem do Webhook..."
az acr build --registry $ACR_NAME --image whatsapp-webhook:latest --file Dockerfile.webhook .

echo "📦 2. Enviando o código para o Azure construir a imagem do Worker..."
az acr build --registry $ACR_NAME --image whatsapp-worker:latest --file Dockerfile.worker .

echo "🚢 3. Atualizando a imagem do Webhook..."
az containerapp update \
  --name webhook-app \
  --resource-group $RESOURCE_GROUP \
  --image ${ACR_NAME}.azurecr.io/whatsapp-webhook:latest \
  --set-env-vars FORCE_UPDATE=$(date +%s)

echo "🚢 4. Atualizando a imagem do Worker (LLM)..."
az containerapp update \
  --name worker-llm \
  --resource-group $RESOURCE_GROUP \
  --image ${ACR_NAME}.azurecr.io/whatsapp-worker:latest \
  --set-env-vars FORCE_UPDATE=$(date +%s)

echo "🚢 5. Atualizando a imagem do Worker (Scheduler)..."
az containerapp update \
  --name worker-scheduler \
  --resource-group $RESOURCE_GROUP \
  --image ${ACR_NAME}.azurecr.io/whatsapp-worker:latest \
  --set-env-vars FORCE_UPDATE=$(date +%s)

echo "✅ Deploy Completo! A infraestrutura e regras KEDA agora são 100% gerenciadas pelo Bicep."
echo "Para pegar a URL pública do seu Webhook (para colar na Meta), rode:"
echo "az containerapp show -n webhook-app -g $RESOURCE_GROUP --query properties.configuration.ingress.fqdn -o tsv"
