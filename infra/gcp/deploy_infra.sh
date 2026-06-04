#!/bin/bash
set -e
# ============================================
# Deploy de Infraestrutura — GCP (gcloud CLI)
# ============================================
# Provisiona: Cloud SQL (PostgreSQL), Memorystore (Redis),
#             Artifact Registry, Secret Manager
#
# Pré-requisitos:
#   1. gcloud CLI instalado e autenticado (gcloud auth login)
#   2. Projeto GCP selecionado (gcloud config set project <ID>)
#   3. APIs habilitadas (executadas automaticamente abaixo)
#   4. Arquivo .env com as chaves de API

PROJECT=$(gcloud config get-value project)
REGION="southamerica-east1"
AR_REPO="whatsapp-assistant"
PG_INSTANCE="whatsapp-pg"
PG_DB="whatsapp_assistant"
PG_USER="pgadmin"
REDIS_INSTANCE="whatsapp-redis"

echo "🔧 Habilitando APIs necessárias..."
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com

# --- 1. Artifact Registry ---
echo "📦 1. Criando Artifact Registry (Docker)..."
gcloud artifacts repositories create $AR_REPO \
  --repository-format=docker \
  --location=$REGION \
  --description="WhatsApp Teacher Assistant images" \
  2>/dev/null || echo "   (repositório já existe)"

# --- 2. Cloud SQL (PostgreSQL 16) ---
PG_PASSWORD=$(openssl rand -base64 16)
echo "🐘 2. Criando Cloud SQL (PostgreSQL 16 - db-f1-micro para FinOps)..."
gcloud sql instances create $PG_INSTANCE \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=$REGION \
  --storage-size=10GB \
  --storage-type=HDD \
  --no-backup \
  --root-password="$PG_PASSWORD" \
  2>/dev/null || echo "   (instância já existe)"

echo "   Criando banco de dados..."
gcloud sql databases create $PG_DB \
  --instance=$PG_INSTANCE \
  2>/dev/null || echo "   (banco já existe)"

echo "   Criando usuário..."
gcloud sql users create $PG_USER \
  --instance=$PG_INSTANCE \
  --password="$PG_PASSWORD" \
  2>/dev/null || echo "   (usuário já existe)"

# --- 3. Memorystore (Redis) ---
echo "🔴 3. Criando Memorystore Redis (Basic 1GB)..."
gcloud redis instances create $REDIS_INSTANCE \
  --size=1 \
  --region=$REGION \
  --redis-version=redis_7_0 \
  --tier=basic \
  2>/dev/null || echo "   (instância já existe)"

# --- 4. Secret Manager ---
echo "🔑 4. Armazenando segredos no Secret Manager..."
export $(grep -v '^#' .env | xargs)

declare -A SECRETS=(
  ["WHATSAPP_TOKEN"]="$WHATSAPP_TOKEN"
  ["META_VERIFY_TOKEN"]="$META_VERIFY_TOKEN"
  ["META_APP_SECRET"]="$META_APP_SECRET"
  ["GOOGLE_API_KEY"]="$GOOGLE_API_KEY"
  ["DATABASE_URL"]="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@//cloudsql/${PROJECT}:${REGION}:${PG_INSTANCE}/${PG_DB}"
)

for key in "${!SECRETS[@]}"; do
  echo -n "${SECRETS[$key]}" | gcloud secrets create "$key" --data-file=- \
    2>/dev/null || echo -n "${SECRETS[$key]}" | gcloud secrets versions add "$key" --data-file=-
  echo "   ✓ $key"
done

# --- Resumo ---
REDIS_HOST=$(gcloud redis instances describe $REDIS_INSTANCE --region=$REGION --format="value(host)" 2>/dev/null)
PG_CONNECTION=$(gcloud sql instances describe $PG_INSTANCE --format="value(connectionName)" 2>/dev/null)

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Infraestrutura GCP criada com sucesso!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📦 Artifact Registry: ${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}"
echo "  🐘 Cloud SQL:         ${PG_CONNECTION}"
echo "  🔴 Redis:             ${REDIS_HOST}:6379"
echo "  🔑 Secrets:           ${#SECRETS[@]} secrets armazenados"
echo ""
echo "  Senha PostgreSQL: $PG_PASSWORD"
echo "  (Salve-a agora, pois não será exibida novamente)"
echo ""
