# ==========================================
# LOCAL OPERATIONS — Infrastructure & CI/CD
# ==========================================

ENV = development
PYTHONPATH_VAL = .

# Globals to avoid repetition
export PYTHONPATH=$(PYTHONPATH_VAL)
export ENVIRONMENT=$(ENV)

.PHONY: help dev dev-api dev-seed test test-integration test-all start

help:
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  WhatsApp Teacher Assistant — Commands"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "  🔧 LOCAL DEVELOPMENT"
	@echo "  make dev                    - Start infrastructure (docker-compose)"
	@echo "  make dev-api                - Start FastAPI server locally"
	@echo "  make dev-worker-<name>      - Start a specific worker locally"
	@echo "                                Options: llm, ingestion, scheduler"
	@echo "  make start                  - Start API, Workers (LLM & Scheduler), and Ngrok"
	@echo "  make dev-seed               - Seed local database with test data"
	@echo ""
	@echo "  🧪 TESTING"
	@echo "  make test                   - Run unit tests"
	@echo "  make test-integration       - Run integration tests"
	@echo "  make test-all               - Run all tests with coverage"
	@echo ""
	@echo "  ☁️ CLOUD DEPLOYMENT"
	@echo "  make deploy-webhook         - Build and deploy Webhook to Azure"
	@echo "  make deploy-worker          - Build and deploy Worker LLM to Azure"
	@echo "  make deploy-scheduler       - Build and deploy Worker Scheduler to Azure"
	@echo "  make deploy-all             - Deploy all components to Azure"
	@echo "  make deploy-infra           - Provision all Azure resources via Bicep"
	@echo "  make destroy-cloud          - Delete ALL Azure resources (CAREFUL!)"
	@echo ""

# ==========================================
# 1. LOCAL DEVELOPMENT
# ==========================================
dev:
	@echo "[INFO] Starting local infrastructure with docker-compose..."
	docker-compose up -d
	@echo "[INFO] Waiting 60s for Service Bus Emulator to initialize internal SQL..."
	sleep 60
	@echo "[INFO] Stack running: Redis, Azurite (Service Bus Emulator), Qdrant"

dev-api:
	@echo "[INFO] Starting FastAPI webhook server..."
	.venv/bin/uvicorn src.webhook.main:app --reload --host 0.0.0.0 --port 8000

# Pattern Rule for all workers
# Usage: make dev-worker-llm, make dev-worker-ingestion, make dev-worker-scheduler
dev-worker-%:
	@echo "[INFO] Starting Worker: $* ..."
	.venv/bin/python -m src.workers.$*_worker

start:
	@.venv/bin/python scripts/start_ngrok.py
	@echo "[INFO] Starting API and Workers in parallel... (Press Ctrl+C to stop all)"
	@trap 'kill 0' INT; \
	make dev-api & \
	make dev-worker-llm & \
	make dev-worker-scheduler & \
	wait

dev-seed:
	@echo "[INFO] Seeding local database..."
	.venv/bin/python scripts/seed_local_db.py

# ==========================================
# 2. TESTING
# ==========================================
test:
	@echo "[INFO] Running unit tests..."
	pytest tests/unit/ -v

test-integration:
	@echo "[INFO] Running integration tests..."
	pytest tests/integration/ -v

test-all:
	@echo "[INFO] Running full test suite with coverage..."
	pytest tests/ -v --cov=src --cov-report=term-missing

# ==========================================
# 3. CLOUD DEPLOYMENT (Azure)
# ==========================================
ACR_NAME ?= $(shell az acr list -g rg-whatsapp-assistant-prod --query "[0].name" -o tsv 2>/dev/null)

deploy-webhook:
	@echo "[INFO] Building Webhook image in Azure Container Registry: $(ACR_NAME)..."
	az acr build --registry $(ACR_NAME) --image whatsapp-webhook:latest --file Dockerfile.webhook .
	@echo "[INFO] Restarting Webhook Container App..."
	az containerapp update -n webhook-app -g rg-whatsapp-assistant-prod --set-env-vars FORCE_RESTART=$(shell date +%s) -o none
	@echo "[SUCCESS] Webhook deployed!"

deploy-worker:
	@echo "[INFO] Building Worker image in Azure Container Registry: $(ACR_NAME)..."
	az acr build --registry $(ACR_NAME) --image whatsapp-worker:latest --file Dockerfile.worker .
	@echo "[INFO] Restarting Worker Container App..."
	az containerapp update -n worker-llm -g rg-whatsapp-assistant-prod --set-env-vars FORCE_RESTART=$(shell date +%s) -o none
	@echo "[SUCCESS] Worker deployed!"

deploy-scheduler:
	@echo "[INFO] Building Worker image in Azure Container Registry: $(ACR_NAME)..."
	az acr build --registry $(ACR_NAME) --image whatsapp-worker:latest --file Dockerfile.worker .
	@echo "[INFO] Restarting Scheduler Container App..."
	az containerapp update -n worker-scheduler -g rg-whatsapp-assistant-prod --set-env-vars FORCE_RESTART=$(shell date +%s) -o none
	@echo "[SUCCESS] Scheduler deployed!"

deploy-all: deploy-webhook deploy-worker deploy-scheduler
	@echo "[SUCCESS] All cloud components deployed successfully!"

deploy-infra:
	@echo "[INFO] Provisioning infrastructure using Bicep..."
	bash infra/deploy_infra.sh

destroy-cloud:
	@echo "[WARNING] Initiating total destruction of cloud resources..."
	@echo "This will delete the entire Resource Group rg-whatsapp-assistant-prod."
	@echo "If you are sure, run this command manually to confirm:"
	@echo "az group delete --name rg-whatsapp-assistant-prod --yes --no-wait"
