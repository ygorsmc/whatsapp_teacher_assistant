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
