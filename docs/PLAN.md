# Plano de Orquestração - Cloud Deployment (24/7)

**Objetivo:** Preparar o projeto WhatsApp Teacher Assistant para implantação em produção na nuvem (Azure), garantindo alta disponibilidade (24/7), escalabilidade via KEDA e segurança com Managed Identities.

## 🔴 User Review Required

> [!IMPORTANT]
> **Aprovação Necessária**
> Por favor, revise este plano. Ao aprovar, iniciaremos a **Fase 2 (Implementação)** disparando agentes em paralelo para codificar infraestrutura, banco de dados e DevOps.

## 🔒 Decisões Arquiteturais Fechadas (Aprovadas)

1. **Banco de Dados:** **Azure SQL (DTU Basic).**
   * *Justificativa de Custo:* O Azure SQL DTU Basic custa em torno de ~$5 USD/mês, sendo a opção gerenciada mais barata do Azure. O PostgreSQL Flexible Server mais barato (Burstable B1ms) custa cerca de ~$13 USD/mês. Como as nossas relações de dados são simples (Alunos, Turmas, Progresso), o Azure SQL entrega o melhor custo-benefício. Usaremos `mssql+aioodbc` no SQLAlchemy.
2. **Infra as Code (IaC):** **Arquivos Bicep.**
   * *Justificativa:* O Bicep é nativo da Microsoft, perfeito para Azure Container Apps e Managed Identities, e não exige gerenciamento de "arquivos de estado" complexos como o Terraform.
3. **Qdrant:** **Qdrant Cloud (Free Tier).**
   * *Justificativa:* O Qdrant Cloud oferece 1GB de RAM e 0.5 vCPU grátis para sempre. Como implementamos a compressão *Scalar Quantization INT8* (que reduz a memória em 75%), esse 1GB é suficiente para suportar cerca de ~1 milhão de vetores. No painel do Qdrant Cloud (Dashboard), você conseguirá acompanhar um gráfico em tempo real de uso de RAM e Disco. Quando bater 80%, você saberá que é hora de considerar o upgrade (que custa a partir de ~$20/mês), mas isso vai demorar MUITO para acontecer no escopo atual.

---

## 🛠️ Proposed Changes (Fase 2)

### 1. Database & State Management (`database-architect`)
*   **[MODIFY]** `src/db/config.py` e repositórios para suportar dialect do SQL Server (`mssql+aioodbc`) ou PostgreSQL.
*   **[NEW]** Migrações Alembic configuradas para o banco de produção.
*   **[MODIFY]** Verificação da persistência do Redis para garantir que a imagem no Azure suporte os scripts Lua atômicos de debounce.

### 2. Infra & Docker (`devops-engineer`)
*   **[NEW]** `Dockerfile.webhook` - Otimizado para FastAPI (Uvicorn) rodando na porta 80.
*   **[NEW]** `Dockerfile.worker` - Imagem headless para rodar o Service Bus consumer.
*   **[NEW]** Manifestos KEDA (`keda-scaler.yaml`) para o Azure Container Apps escalar baseado no tamanho da fila `inbound_messages`.
*   **[NEW]** Configuração do GitHub Actions (`.github/workflows/deploy.yml`) para CI/CD automatizado.

### 3. Security & Cloud Native Auth (`security-auditor`)
*   **[MODIFY]** Substituir leitura estática de chaves no `.env` pelo pacote `azure-identity` (`DefaultAzureCredential`).
*   **[NEW]** Integração com o Azure Key Vault para buscar tokens de API (OpenAI, Gemini, Meta) em runtime, abolindo chaves chumbadas no ambiente.
*   **[MODIFY]** `src/shared/config.py` para suportar fallback (Key Vault em prod, `.env` em local).

### 4. Telemetry & Observability (`backend-specialist`)
*   **[NEW]** Integração do `structlog` com o Azure Application Insights (Azure Monitor) via `azure-monitor-opentelemetry`.
*   **[MODIFY]** `src/shared/telemetry.py` para injetar automaticamente o contexto de tracing.

---

## ✅ Verification Plan

### Testes Automatizados e Manuais
- Execução do checklist de segurança: `python .agent/scripts/checklist.py .`
- Subir os containers localmente usando `docker-compose.yml` simulando o ambiente Azure.
- Teste de Carga E2E (via Locust) enviando 100 mensagens simultâneas para o Webhook para garantir o Auto-Scale do KEDA.
- Validar logs no console para confirmar ausência de chaves de API expostas (Security Audit).
