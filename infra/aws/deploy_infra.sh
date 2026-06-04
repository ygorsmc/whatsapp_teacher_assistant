#!/bin/bash
set -e
# ============================================
# Deploy de Infraestrutura — AWS (CLI)
# ============================================
# Provisiona: RDS PostgreSQL, ElastiCache Redis,
#             ECR, Secrets Manager, ECS Fargate Cluster
#
# Pré-requisitos:
#   1. AWS CLI instalado e configurado (aws configure)
#   2. VPC default disponível na região
#   3. Arquivo .env com as chaves de API

REGION="sa-east-1"
CLUSTER_NAME="whatsapp-assistant"
PG_INSTANCE="whatsapp-pg"
PG_DB="whatsapp_assistant"
PG_USER="pgadmin"
REDIS_NAME="whatsapp-redis"

echo "🔧 Verificando configuração da AWS..."
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "   Conta: $AWS_ACCOUNT | Região: $REGION"

# --- 1. ECR Repositories ---
echo "📦 1. Criando repositórios ECR..."
for REPO in whatsapp-webhook whatsapp-worker; do
  aws ecr describe-repositories --repository-names $REPO --region $REGION 2>/dev/null || \
    aws ecr create-repository \
      --repository-name $REPO \
      --region $REGION \
      --image-scanning-configuration scanOnPush=true \
      --encryption-configuration encryptionType=AES256
  echo "   ✓ $REPO"
done

# --- 2. RDS PostgreSQL ---
PG_PASSWORD=$(openssl rand -base64 16)
echo "🐘 2. Criando RDS PostgreSQL (db.t3.micro — Free Tier elegível)..."
aws rds create-db-instance \
  --db-instance-identifier $PG_INSTANCE \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version "16.4" \
  --master-username $PG_USER \
  --master-user-password "$PG_PASSWORD" \
  --allocated-storage 20 \
  --storage-type gp3 \
  --db-name $PG_DB \
  --no-multi-az \
  --backup-retention-period 7 \
  --publicly-accessible \
  --region $REGION \
  2>/dev/null || echo "   (instância já existe)"

echo "   Aguardando RDS ficar disponível (isso pode levar 5-10 min)..."
aws rds wait db-instance-available --db-instance-identifier $PG_INSTANCE --region $REGION

PG_ENDPOINT=$(aws rds describe-db-instances \
  --db-instance-identifier $PG_INSTANCE \
  --region $REGION \
  --query "DBInstances[0].Endpoint.Address" --output text)

# --- 3. ElastiCache Redis ---
echo "🔴 3. Criando ElastiCache Redis (cache.t3.micro — Free Tier elegível)..."
aws elasticache create-cache-cluster \
  --cache-cluster-id $REDIS_NAME \
  --cache-node-type cache.t3.micro \
  --engine redis \
  --engine-version "7.1" \
  --num-cache-nodes 1 \
  --region $REGION \
  2>/dev/null || echo "   (cluster já existe)"

echo "   Aguardando Redis ficar disponível..."
aws elasticache wait cache-cluster-available --cache-cluster-id $REDIS_NAME --region $REGION 2>/dev/null || true

REDIS_ENDPOINT=$(aws elasticache describe-cache-clusters \
  --cache-cluster-id $REDIS_NAME \
  --show-cache-node-info \
  --region $REGION \
  --query "CacheClusters[0].CacheNodes[0].Endpoint.Address" --output text 2>/dev/null)

# --- 4. ECS Cluster ---
echo "🐳 4. Criando ECS Fargate Cluster..."
aws ecs create-cluster \
  --cluster-name $CLUSTER_NAME \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
  --region $REGION \
  2>/dev/null || echo "   (cluster já existe)"

# --- 5. Secrets Manager ---
echo "🔑 5. Armazenando segredos no Secrets Manager..."
export $(grep -v '^#' .env | xargs)

DATABASE_URL="postgresql+asyncpg://${PG_USER}:${PG_PASSWORD}@${PG_ENDPOINT}:5432/${PG_DB}"

SECRET_JSON=$(cat <<EOF
{
  "WHATSAPP_TOKEN": "$WHATSAPP_TOKEN",
  "META_VERIFY_TOKEN": "$META_VERIFY_TOKEN",
  "META_APP_SECRET": "$META_APP_SECRET",
  "GOOGLE_API_KEY": "$GOOGLE_API_KEY",
  "DATABASE_URL": "$DATABASE_URL",
  "REDIS_URL": "redis://${REDIS_ENDPOINT}:6379"
}
EOF
)

aws secretsmanager create-secret \
  --name whatsapp-assistant-secrets \
  --secret-string "$SECRET_JSON" \
  --region $REGION \
  2>/dev/null || \
aws secretsmanager update-secret \
  --secret-id whatsapp-assistant-secrets \
  --secret-string "$SECRET_JSON" \
  --region $REGION

echo "   ✓ Todos os segredos armazenados"

# --- Resumo ---
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Infraestrutura AWS criada com sucesso!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📦 ECR:       ${AWS_ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
echo "  🐘 RDS:       ${PG_ENDPOINT}"
echo "  🔴 Redis:     ${REDIS_ENDPOINT}:6379"
echo "  🐳 ECS:       ${CLUSTER_NAME}"
echo ""
echo "  Senha PostgreSQL: $PG_PASSWORD"
echo "  (Salve-a agora, pois não será exibida novamente)"
echo ""
echo "⚠️  Próximo passo: Crie Task Definitions e Services no ECS."
echo "     Ou use: bash infra/aws/deploy_app.sh"
echo ""
