# ==========================================
# CONFIGURAÇÃO PRINCIPAL
# ==========================================
# Escolha o provedor de nuvem para o qual deseja fazer deploy.
# Opções válidas: azure | gcp | aws
TARGET_CLOUD ?= azure

# --- Chave liga/desliga do deploy automático LOCAL (medida TEMPORÁRIA) ---
# A assinatura "Azure for Students" no tenant da PUCPR não permite criar
# Service Principal, então o GitHub Actions não consegue autenticar/deployar.
# Enquanto isso, `make ship` faz o deploy a partir da sua máquina (az login).
#   true  -> após os testes passarem, `make ship` deploya local no Azure.
#   false -> `make ship` só faz push; o deploy fica a cargo do GitHub Actions.
# Pode sobrescrever na chamada: `make ship AUTO_DEPLOY=false`.
AUTO_DEPLOY ?= true

# ==========================================
# LOCAL OPERATIONS — Infrastructure & CI/CD
# ==========================================

ENV = development
PYTHONPATH_VAL = .

# Globals to avoid repetition
export PYTHONPATH=$(PYTHONPATH_VAL)
export ENVIRONMENT=$(ENV)
export CLOUD=$(TARGET_CLOUD)

# --- Azure Defaults ---
AZURE_RG          ?= rg-whatsapp-assistant-prod
ACR_NAME          ?= $(shell az acr list -g $(AZURE_RG) --query "[0].name" -o tsv 2>/dev/null)

# --- GCP Defaults ---
GCP_PROJECT       ?= $(shell gcloud config get-value project 2>/dev/null)
GCP_REGION        ?= southamerica-east1
GCP_AR_REPO       ?= whatsapp-assistant
GCP_AR_HOST       ?= $(GCP_REGION)-docker.pkg.dev/$(GCP_PROJECT)/$(GCP_AR_REPO)

# --- AWS Defaults ---
AWS_REGION        ?= sa-east-1
AWS_ACCOUNT_ID    ?= $(shell aws sts get-caller-identity --query Account --output text 2>/dev/null)
AWS_ECR_REPO      ?= $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com

.PHONY: help dev dev-api dev-seed env-local env-prod env-status db-upgrade db-reset test test-integration test-all start \
        ship deploy-webhook deploy-worker deploy-scheduler deploy-all deploy-infra destroy-cloud

help:
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  WhatsApp Teacher Assistant — Commands"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "  🔧 LOCAL DEVELOPMENT"
	@echo "  make env-local              - Switch .env to LOCAL profile (all local)"
	@echo "  make env-prod               - Switch .env to PROD profile (Qdrant cloud)"
	@echo "  make env-status             - Show the active environment profile"
	@echo "  make dev                    - Start infrastructure (docker compose)"
	@echo "  make dev-api                - Start FastAPI server locally"
	@echo "  make dev-worker-<name>      - Start a specific worker locally"
	@echo "                                Options: llm, ingestion, scheduler"
	@echo "  make start                  - Start API, Workers (LLM & Scheduler), and Ngrok"
	@echo "  make db-upgrade             - Apply Alembic migrations (alembic upgrade head)"
	@echo "  make db-reset               - Drop & recreate local Postgres schema, then migrate"
	@echo "  make dev-seed               - Seed local database with test data"
	@echo ""
	@echo "  🧪 TESTING"
	@echo "  make test                   - Run unit tests"
	@echo "  make test-integration       - Run integration tests"
	@echo "  make test-all               - Run all tests with coverage"
	@echo ""
	@echo "  ☁️  CLOUD DEPLOYMENT (CLOUD=azure|gcp|aws)"
	@echo "  make deploy-webhook         - Build and deploy Webhook"
	@echo "  make deploy-worker          - Build and deploy Worker LLM"
	@echo "  make deploy-scheduler       - Build and deploy Worker Scheduler"
	@echo "  make deploy-all             - Deploy all components"
	@echo "  make deploy-infra           - Provision cloud resources"
	@echo "  make destroy-cloud          - Delete ALL cloud resources (CAREFUL!)"
	@echo ""
	@echo "  Current CLOUD provider: $(CLOUD)"
	@echo ""

# ==========================================
# 1. LOCAL DEVELOPMENT
# ==========================================
dev:
	@echo "[INFO] Starting local infrastructure with docker compose..."
	docker compose up -d
	@echo "[INFO] Waiting for services to initialize..."
	sleep 10
	@echo "[INFO] Stack running: Redis, PostgreSQL, Qdrant"

dev-api:
	@echo "[INFO] Starting FastAPI webhook server..."
	.venv/bin/uvicorn src.webhook.main:app --reload --host 0.0.0.0 --port 8000

# Pattern Rule for all workers — com auto-restart em caso de FALHA (on-failure).
# Reinicia quando o worker morre por ERRO: exit 1..127 OU crash nativo via sinal
# fatal (SIGABRT=134, SIGSEGV=139, SIGBUS=135...). Encerra de vez SÓ em saída
# limpa (0) ou desligamento pedido (Ctrl+C=130 / SIGTERM=143) — não vira loop no
# Ctrl+C, mas TAMBÉM não trata um core dump como se fosse desligamento limpo.
# Guarda anti crash-loop: 5 quedas seguidas em <5s (ex.: erro de import) → desiste.
# Usage: make dev-worker-llm, make dev-worker-ingestion, make dev-worker-scheduler
dev-worker-%:
	@echo "[INFO] Starting Worker: $* (auto-restart on failure) ..."
	@fails=0; \
	while true; do \
		started=$$(date +%s); \
		.venv/bin/python -m src.workers.$*_worker; \
		code=$$?; \
		if [ $$code -eq 0 ] || [ $$code -eq 130 ] || [ $$code -eq 143 ]; then \
			echo "[INFO] Worker $* exited (code $$code). Not restarting."; \
			break; \
		fi; \
		if [ $$(( $$(date +%s) - $$started )) -lt 5 ]; then \
			fails=$$((fails + 1)); \
		else \
			fails=0; \
		fi; \
		if [ $$fails -ge 5 ]; then \
			echo "[ERROR] Worker $* crashed 5x in a row (code $$code). Giving up — fix the error and rerun."; \
			exit $$code; \
		fi; \
		echo "[WARN] Worker $* crashed (code $$code). Restarting in 2s... (fail $$fails/5)"; \
		sleep 2; \
	done

start:
	@.venv/bin/python scripts/start_ngrok.py
	@echo "[INFO] Starting API and Workers in parallel... (Press Ctrl+C to stop all)"
	@trap 'kill 0' INT; \
	make dev-api & \
	make dev-worker-llm & \
	make dev-worker-scheduler & \
	wait

# ── Troca de ambiente (perfis .env.local / .env.prod) ──────────────────────
# Dois mecanismos:
#   1) Persistente:  make env-local | make env-prod   (sobrescreve o .env ativo)
#   2) Pontual:      ENV_FILE=.env.prod make <alvo>    (sem trocar o .env)
# DB e Redis ficam SEMPRE locais; o que muda no modo prod é o Qdrant (cloud).
env-local:
	@cp .env.local .env
	@echo "🟢 Ambiente ATIVO: LOCAL  (Postgres, Redis e Qdrant locais)"

env-prod:
	@cp .env.prod .env
	@echo "🔴 Ambiente ATIVO: PROD  (Qdrant CLOUD — escritas afetam dados REAIS!)"
	@echo "   DB/Redis seguem locais. Para operar o banco real de prod, use 'az containerapp exec'."

env-status:
	@printf "Ambiente atual (.env): "; (grep -m1 '^APP_ENV=' .env 2>/dev/null | cut -d= -f2) || echo "indefinido"
	@grep -m1 '^QDRANT_URL=' .env 2>/dev/null | sed 's/^/  /'

db-upgrade:
	@echo "[INFO] Applying Alembic migrations (alembic upgrade head)..."
	.venv/bin/alembic upgrade head

db-reset:
	@echo "[WARN] Dropping and recreating the local Postgres schema (public)..."
	docker compose exec -T postgres psql -U whatsapp -d whatsapp_assistant -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	$(MAKE) db-upgrade

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
# 2b. SHIP — testar, enviar e (se AUTO_DEPLOY) deployar localmente
# ==========================================
# Fluxo único enquanto o deploy via CI está indisponível (ver AUTO_DEPLOY no topo).
# Roda o gate local (pytest, espelho do CI), faz o push e — se AUTO_DEPLOY=true —
# deploya da sua máquina. Ruff/Mypy continuam sendo validados pelo GitHub Actions.
ship:
	@echo "[SHIP 1/3] Quality gate local (pytest — espelho do CI)..."
	pytest tests/
	@echo "[SHIP 2/3] Enviando código para o GitHub (origin/main)..."
	git push origin main
	@if [ "$(AUTO_DEPLOY)" = "true" ]; then \
		echo "[SHIP 3/3] AUTO_DEPLOY=true -> deploy LOCAL no Azure (requer 'az login')..."; \
		$(MAKE) deploy-all; \
	else \
		echo "[SHIP 3/3] AUTO_DEPLOY=false -> push feito; deploy delegado ao GitHub Actions."; \
	fi
	@echo "[SHIP] Concluido."

# ==========================================
# 3. CLOUD DEPLOYMENT (Multi-Cloud Router)
# ==========================================
# The user picks the cloud via:  make deploy-webhook CLOUD=gcp
# Internally we delegate to _deploy-<component>-<cloud>

deploy-webhook:
	@$(MAKE) _deploy-webhook-$(CLOUD)

deploy-worker:
	@$(MAKE) _deploy-worker-$(CLOUD)

deploy-scheduler:
	@$(MAKE) _deploy-scheduler-$(CLOUD)

deploy-all: deploy-webhook deploy-worker deploy-scheduler
	@echo "[SUCCESS] All components deployed to $(CLOUD)!"

# ------------------------------------------
# 3a. AZURE (Container Apps + ACR)
# ------------------------------------------
_deploy-webhook-azure:
	@echo "[AZURE] Building Webhook image in ACR: $(ACR_NAME)..."
	az acr build --registry $(ACR_NAME) --image whatsapp-webhook:latest --file Dockerfile.webhook .
	@echo "[AZURE] Updating Webhook Container App..."
	az containerapp update -n webhook-app -g $(AZURE_RG) --set-env-vars FORCE_RESTART=$(shell date +%s) -o none
	@echo "[SUCCESS] Webhook deployed to Azure!"

_deploy-worker-azure:
	@echo "[AZURE] Building Worker image in ACR: $(ACR_NAME)..."
	az acr build --registry $(ACR_NAME) --image whatsapp-worker:latest --file Dockerfile.worker .
	@echo "[AZURE] Updating Worker Container App..."
	az containerapp update -n worker-llm -g $(AZURE_RG) --set-env-vars FORCE_RESTART=$(shell date +%s) -o none
	@echo "[SUCCESS] Worker deployed to Azure!"

_deploy-scheduler-azure:
	@echo "[AZURE] Building Scheduler image in ACR: $(ACR_NAME)..."
	az acr build --registry $(ACR_NAME) --image whatsapp-worker:latest --file Dockerfile.worker .
	@echo "[AZURE] Updating Scheduler Container App..."
	az containerapp update -n worker-scheduler -g $(AZURE_RG) --set-env-vars FORCE_RESTART=$(shell date +%s) -o none
	@echo "[SUCCESS] Scheduler deployed to Azure!"

# ------------------------------------------
# 3b. GCP (Cloud Run + Artifact Registry)
# ------------------------------------------
_deploy-webhook-gcp:
	@echo "[GCP] Building Webhook image via Cloud Build..."
	gcloud builds submit --tag $(GCP_AR_HOST)/whatsapp-webhook:latest --timeout=900 .
	@echo "[GCP] Deploying Webhook to Cloud Run..."
	gcloud run deploy webhook-app \
		--image $(GCP_AR_HOST)/whatsapp-webhook:latest \
		--region $(GCP_REGION) \
		--platform managed \
		--allow-unauthenticated \
		--port 8080 \
		--memory 512Mi \
		--cpu 1 \
		--min-instances 1 \
		--max-instances 10 \
		--set-env-vars QUEUE_BACKEND=redis,PARSER_BACKEND=azure,ENVIRONMENT=production
	@echo "[SUCCESS] Webhook deployed to GCP Cloud Run!"

_deploy-worker-gcp:
	@echo "[GCP] Building Worker image via Cloud Build..."
	gcloud builds submit --tag $(GCP_AR_HOST)/whatsapp-worker:latest --timeout=900 .
	@echo "[GCP] Deploying Worker to Cloud Run (always-on)..."
	gcloud run deploy worker-llm \
		--image $(GCP_AR_HOST)/whatsapp-worker:latest \
		--region $(GCP_REGION) \
		--platform managed \
		--no-allow-unauthenticated \
		--memory 1Gi \
		--cpu 1 \
		--min-instances 1 \
		--max-instances 4 \
		--set-env-vars WORKER_TYPE=llm,QUEUE_BACKEND=redis,PARSER_BACKEND=azure,ENVIRONMENT=production \
		--command "python","-m","src.workers.llm_worker"
	@echo "[SUCCESS] Worker deployed to GCP Cloud Run!"

_deploy-scheduler-gcp:
	@echo "[GCP] Building Scheduler image via Cloud Build..."
	gcloud builds submit --tag $(GCP_AR_HOST)/whatsapp-worker:latest --timeout=900 .
	@echo "[GCP] Deploying Scheduler to Cloud Run..."
	gcloud run deploy worker-scheduler \
		--image $(GCP_AR_HOST)/whatsapp-worker:latest \
		--region $(GCP_REGION) \
		--platform managed \
		--no-allow-unauthenticated \
		--memory 512Mi \
		--cpu 1 \
		--min-instances 1 \
		--max-instances 2 \
		--set-env-vars WORKER_TYPE=scheduler,QUEUE_BACKEND=redis,PARSER_BACKEND=azure,ENVIRONMENT=production \
		--command "python","-m","src.workers.scheduler_worker"
	@echo "[SUCCESS] Scheduler deployed to GCP Cloud Run!"

# ------------------------------------------
# 3c. AWS (ECS Fargate + ECR)
# ------------------------------------------
_ecr-login:
	@echo "[AWS] Authenticating with ECR..."
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(AWS_ECR_REPO)

_ensure-ecr-repo:
	@aws ecr describe-repositories --repository-names $(1) --region $(AWS_REGION) 2>/dev/null || \
		aws ecr create-repository --repository-name $(1) --region $(AWS_REGION) --image-scanning-configuration scanOnPush=true

_deploy-webhook-aws: _ecr-login
	@echo "[AWS] Building Webhook image locally and pushing to ECR..."
	$(MAKE) _ensure-ecr-repo 1=whatsapp-webhook
	docker build -f Dockerfile.webhook -t $(AWS_ECR_REPO)/whatsapp-webhook:latest .
	docker push $(AWS_ECR_REPO)/whatsapp-webhook:latest
	@echo "[AWS] Updating ECS Service (webhook)..."
	aws ecs update-service \
		--cluster whatsapp-assistant \
		--service webhook-app \
		--force-new-deployment \
		--region $(AWS_REGION)
	@echo "[SUCCESS] Webhook deployed to AWS ECS!"

_deploy-worker-aws: _ecr-login
	@echo "[AWS] Building Worker image locally and pushing to ECR..."
	$(MAKE) _ensure-ecr-repo 1=whatsapp-worker
	docker build -f Dockerfile.worker -t $(AWS_ECR_REPO)/whatsapp-worker:latest .
	docker push $(AWS_ECR_REPO)/whatsapp-worker:latest
	@echo "[AWS] Updating ECS Service (worker-llm)..."
	aws ecs update-service \
		--cluster whatsapp-assistant \
		--service worker-llm \
		--force-new-deployment \
		--region $(AWS_REGION)
	@echo "[SUCCESS] Worker deployed to AWS ECS!"

_deploy-scheduler-aws: _ecr-login
	@echo "[AWS] Building Scheduler image locally and pushing to ECR..."
	$(MAKE) _ensure-ecr-repo 1=whatsapp-worker
	docker build -f Dockerfile.worker -t $(AWS_ECR_REPO)/whatsapp-worker:latest .
	docker push $(AWS_ECR_REPO)/whatsapp-worker:latest
	@echo "[AWS] Updating ECS Service (scheduler)..."
	aws ecs update-service \
		--cluster whatsapp-assistant \
		--service worker-scheduler \
		--force-new-deployment \
		--region $(AWS_REGION)
	@echo "[SUCCESS] Scheduler deployed to AWS ECS!"

# ==========================================
# 4. INFRASTRUCTURE PROVISIONING
# ==========================================
deploy-infra:
ifeq ($(CLOUD),azure)
	@echo "[AZURE] Provisioning infrastructure using Bicep..."
	bash infra/azure/deploy_infra.sh
else ifeq ($(CLOUD),gcp)
	@echo "[GCP] Provisioning infrastructure using gcloud CLI..."
	bash infra/gcp/deploy_infra.sh
else ifeq ($(CLOUD),aws)
	@echo "[AWS] Provisioning infrastructure using AWS CLI..."
	bash infra/aws/deploy_infra.sh
endif

# ==========================================
# 5. TEARDOWN
# ==========================================
destroy-cloud:
ifeq ($(CLOUD),azure)
	@echo "[WARNING] This will delete the entire Resource Group $(AZURE_RG)."
	@echo "Run manually to confirm:"
	@echo "  az group delete --name $(AZURE_RG) --yes --no-wait"
else ifeq ($(CLOUD),gcp)
	@echo "[WARNING] This will delete GCP Cloud Run services."
	@echo "Run manually to confirm:"
	@echo "  gcloud run services delete webhook-app --region $(GCP_REGION) --quiet"
	@echo "  gcloud run services delete worker-llm --region $(GCP_REGION) --quiet"
	@echo "  gcloud run services delete worker-scheduler --region $(GCP_REGION) --quiet"
else ifeq ($(CLOUD),aws)
	@echo "[WARNING] This will stop ECS services."
	@echo "Run manually to confirm:"
	@echo "  aws ecs update-service --cluster whatsapp-assistant --service webhook-app --desired-count 0 --region $(AWS_REGION)"
	@echo "  aws ecs update-service --cluster whatsapp-assistant --service worker-llm --desired-count 0 --region $(AWS_REGION)"
	@echo "  aws ecs delete-cluster --cluster whatsapp-assistant --region $(AWS_REGION)"
endif
