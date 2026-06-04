#!/bin/bash
set -e
# ============================================
# Deploy de Apps — AWS (ECS Fargate)
# ============================================
# Faz build, push para ECR e deploy no ECS Fargate
#
# Pré-requisitos:
#   1. Infraestrutura já provisionada (deploy_infra.sh)
#   2. Task Definitions registradas (task-definitions/*.json)
#   3. AWS CLI autenticado

REGION="sa-east-1"
CLUSTER_NAME="whatsapp-assistant"
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR_HOST="${AWS_ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "🚀 Iniciando Deploy para AWS ECS Fargate..."

# --- 1. Login ECR ---
echo "🔐 1. Autenticando no ECR..."
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_HOST

# --- 2. Build e Push ---
echo "📦 2. Fazendo build da imagem do Webhook..."
docker build -f Dockerfile.webhook -t ${ECR_HOST}/whatsapp-webhook:latest .
docker push ${ECR_HOST}/whatsapp-webhook:latest

echo "📦 3. Fazendo build da imagem do Worker..."
docker build -f Dockerfile.worker -t ${ECR_HOST}/whatsapp-worker:latest .
docker push ${ECR_HOST}/whatsapp-worker:latest

# --- 3. Register Task Definitions ---
echo "📋 4. Registrando Task Definitions..."

# Webhook Task Definition
cat > /tmp/webhook-task.json <<EOF
{
  "family": "webhook-app",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::${AWS_ACCOUNT}:role/ecsTaskExecutionRole",
  "containerDefinitions": [{
    "name": "webhook",
    "image": "${ECR_HOST}/whatsapp-webhook:latest",
    "portMappings": [{"containerPort": 80, "protocol": "tcp"}],
    "environment": [
      {"name": "QUEUE_BACKEND", "value": "redis"},
      {"name": "PARSER_BACKEND", "value": "azure"},
      {"name": "ENVIRONMENT", "value": "production"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/whatsapp-webhook",
        "awslogs-region": "${REGION}",
        "awslogs-stream-prefix": "ecs"
      }
    }
  }]
}
EOF

aws ecs register-task-definition --cli-input-json file:///tmp/webhook-task.json --region $REGION

# Worker LLM Task Definition
cat > /tmp/worker-llm-task.json <<EOF
{
  "family": "worker-llm",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "arn:aws:iam::${AWS_ACCOUNT}:role/ecsTaskExecutionRole",
  "containerDefinitions": [{
    "name": "worker-llm",
    "image": "${ECR_HOST}/whatsapp-worker:latest",
    "command": ["python", "-m", "src.workers.llm_worker"],
    "environment": [
      {"name": "WORKER_TYPE", "value": "llm"},
      {"name": "QUEUE_BACKEND", "value": "redis"},
      {"name": "PARSER_BACKEND", "value": "azure"},
      {"name": "ENVIRONMENT", "value": "production"}
    ],
    "logConfiguration": {
      "logDriver": "awslogs",
      "options": {
        "awslogs-group": "/ecs/whatsapp-worker",
        "awslogs-region": "${REGION}",
        "awslogs-stream-prefix": "ecs"
      }
    }
  }]
}
EOF

aws ecs register-task-definition --cli-input-json file:///tmp/worker-llm-task.json --region $REGION

# --- 4. Create/Update Services ---
echo "🚢 5. Fazendo Deploy dos Services..."

# Busca subnets e security group default
DEFAULT_VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text --region $REGION)
SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$DEFAULT_VPC --query "Subnets[*].SubnetId" --output text --region $REGION | tr '\t' ',')
SG=$(aws ec2 describe-security-groups --filters Name=vpc-id,Values=$DEFAULT_VPC Name=group-name,Values=default --query "SecurityGroups[0].GroupId" --output text --region $REGION)

for SERVICE in webhook-app worker-llm; do
  echo "   Deploying $SERVICE..."
  aws ecs update-service \
    --cluster $CLUSTER_NAME \
    --service $SERVICE \
    --force-new-deployment \
    --region $REGION \
    2>/dev/null || \
  aws ecs create-service \
    --cluster $CLUSTER_NAME \
    --service-name $SERVICE \
    --task-definition $SERVICE \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
    --region $REGION
  echo "   ✓ $SERVICE"
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Deploy AWS ECS concluído!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Use 'aws ecs describe-services --cluster $CLUSTER_NAME' para monitorar."
echo ""
echo "⚠️  Para expor publicamente, configure um Application Load Balancer (ALB)"
echo "     apontando para o service webhook-app na porta 80."
echo ""
