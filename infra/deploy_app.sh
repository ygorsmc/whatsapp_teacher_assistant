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

echo "🔄 3. Atualizando o Container App do Webhook..."
# Primeiro garantimos que o container app existe.
# Como o ambiente pede Log Analytics nativamente, precisamos garantir que o provedor esteja registrado:
echo "⏳ Registrando provider Microsoft.OperationalInsights (necessário para logs)..."
az provider register -n Microsoft.OperationalInsights --wait

echo "⏳ Verificando Ambiente do Container Apps..."
az containerapp env create \
  --name env-whatsapp-bot \
  --resource-group $RESOURCE_GROUP \
  --location brazilsouth || true

echo "🚢 4. Fazendo Deploy do Webhook..."
az containerapp create \
  --name webhook-app \
  --resource-group $RESOURCE_GROUP \
  --environment env-whatsapp-bot \
  --image ${ACR_NAME}.azurecr.io/whatsapp-webhook:latest \
  --target-port 80 \
  --ingress external \
  --registry-server ${ACR_NAME}.azurecr.io \
  --min-replicas 1 \
  --max-replicas 3

echo "🚢 5. Fazendo Deploy do Worker (LLM)..."
az containerapp create \
  --name worker-llm \
  --resource-group $RESOURCE_GROUP \
  --environment env-whatsapp-bot \
  --image ${ACR_NAME}.azurecr.io/whatsapp-worker:latest \
  --registry-server ${ACR_NAME}.azurecr.io \
  --env-vars WORKER_TYPE=llm \
  --min-replicas 0 \
  --max-replicas 10

echo "🚢 6. Fazendo Deploy do Worker (Scheduler)..."
az containerapp create \
  --name worker-scheduler \
  --resource-group $RESOURCE_GROUP \
  --environment env-whatsapp-bot \
  --image ${ACR_NAME}.azurecr.io/whatsapp-worker:latest \
  --registry-server ${ACR_NAME}.azurecr.io \
  --env-vars WORKER_TYPE=scheduler \
  --min-replicas 1 \
  --max-replicas 1

echo "✅ Deploy Completo! Seu bot está rodando na nuvem."
echo "Para pegar a URL pública do seu Webhook (para colar na Meta), rode:"
echo "az containerapp show -n webhook-app -g $RESOURCE_GROUP --query properties.configuration.ingress.fqdn -o tsv"
