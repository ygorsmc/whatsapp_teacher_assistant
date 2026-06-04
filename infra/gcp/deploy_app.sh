#!/bin/bash
set -e
# ============================================
# Deploy de Apps — GCP (Cloud Run)
# ============================================
# Faz build das imagens e deploy no Cloud Run
#
# Pré-requisitos:
#   1. Infraestrutura já provisionada (deploy_infra.sh)
#   2. gcloud CLI autenticado

PROJECT=$(gcloud config get-value project)
REGION="southamerica-east1"
AR_REPO="whatsapp-assistant"
AR_HOST="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}"

PG_INSTANCE=$(gcloud sql instances describe whatsapp-pg --format="value(connectionName)" 2>/dev/null)
REDIS_HOST=$(gcloud redis instances describe whatsapp-redis --region=$REGION --format="value(host)" 2>/dev/null)

echo "🚀 Iniciando Deploy para GCP Cloud Run..."

# --- 1. Build das Imagens ---
echo "📦 1. Fazendo build da imagem do Webhook via Cloud Build..."
gcloud builds submit --tag ${AR_HOST}/whatsapp-webhook:latest --timeout=900 .

echo "📦 2. Fazendo build da imagem do Worker via Cloud Build..."
gcloud builds submit --tag ${AR_HOST}/whatsapp-worker:latest --timeout=900 .

# --- 2. Deploy dos Services ---
echo "🚢 3. Fazendo Deploy do Webhook..."
gcloud run deploy webhook-app \
  --image ${AR_HOST}/whatsapp-webhook:latest \
  --region $REGION \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 10 \
  --add-cloudsql-instances $PG_INSTANCE \
  --set-env-vars "QUEUE_BACKEND=redis,PARSER_BACKEND=azure,ENVIRONMENT=production,REDIS_URL=redis://${REDIS_HOST}:6379"

echo "🚢 4. Fazendo Deploy do Worker LLM..."
gcloud run deploy worker-llm \
  --image ${AR_HOST}/whatsapp-worker:latest \
  --region $REGION \
  --platform managed \
  --no-allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 4 \
  --add-cloudsql-instances $PG_INSTANCE \
  --set-env-vars "WORKER_TYPE=llm,QUEUE_BACKEND=redis,PARSER_BACKEND=azure,ENVIRONMENT=production,REDIS_URL=redis://${REDIS_HOST}:6379" \
  --command "python","-m","src.workers.llm_worker"

echo "🚢 5. Fazendo Deploy do Worker Scheduler..."
gcloud run deploy worker-scheduler \
  --image ${AR_HOST}/whatsapp-worker:latest \
  --region $REGION \
  --platform managed \
  --no-allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 2 \
  --add-cloudsql-instances $PG_INSTANCE \
  --set-env-vars "WORKER_TYPE=scheduler,QUEUE_BACKEND=redis,PARSER_BACKEND=azure,ENVIRONMENT=production,REDIS_URL=redis://${REDIS_HOST}:6379" \
  --command "python","-m","src.workers.scheduler_worker"

# --- Resumo ---
WEBHOOK_URL=$(gcloud run services describe webhook-app --region=$REGION --format="value(status.url)")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Deploy GCP concluído!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🌐 Webhook URL: ${WEBHOOK_URL}"
echo "  (Cole esta URL no Meta Developer Portal como Callback)"
echo ""
