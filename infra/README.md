# Infraestrutura Multi-Cloud

Este diretório contém os scripts de provisionamento e deploy para cada provedor de nuvem suportado.

## Estrutura

```
infra/
├── azure/                    # Microsoft Azure
│   ├── bicep/main.bicep      # IaC declarativo (PostgreSQL, Redis, ACR, Key Vault)
│   ├── deploy_infra.sh       # Provisiona recursos via Bicep
│   ├── deploy_app.sh         # Build ACR + deploy Container Apps
│   ├── worker-llm.yaml       # Manifesto do Container App do Worker LLM
│   └── worker-scheduler.yaml # Manifesto do Container App do Scheduler
│
├── gcp/                      # Google Cloud Platform
│   ├── deploy_infra.sh       # Provisiona Cloud SQL, Memorystore, Artifact Registry, Secret Manager
│   └── deploy_app.sh         # Build via Cloud Build + deploy Cloud Run
│
├── aws/                      # Amazon Web Services
│   ├── deploy_infra.sh       # Provisiona RDS, ElastiCache, ECR, ECS Fargate, Secrets Manager
│   └── deploy_app.sh         # Build local + push ECR + deploy ECS Fargate
│
└── keda-scaler.yaml          # Auto-escalonamento (agnóstico — Redis Streams trigger)
```

## Serviços Equivalentes por Cloud

| Serviço            | Azure                          | GCP                         | AWS                        |
|--------------------|--------------------------------|-----------------------------|----------------------------|
| **Banco de Dados** | Azure Database for PostgreSQL  | Cloud SQL (PostgreSQL)      | RDS (PostgreSQL)           |
| **Cache / Filas**  | Azure Cache for Redis          | Memorystore (Redis)         | ElastiCache (Redis)        |
| **Registry**       | Container Registry (ACR)       | Artifact Registry           | Elastic Container Registry |
| **Compute**        | Container Apps                 | Cloud Run                   | ECS Fargate                |
| **Secrets**        | Key Vault                      | Secret Manager              | Secrets Manager            |
| **Escalonamento**  | KEDA (Container Apps)          | Cloud Run (Nativo via HTTP / Pub/Sub) | Application Auto Scaling (CloudWatch) |
| **IaC**            | Bicep                          | gcloud CLI                  | AWS CLI                    |

## Como Usar

### Via Makefile (recomendado)

```bash
# Altere TARGET_CLOUD no topo do Makefile, ou passe como argumento:

# Provisionar infraestrutura
make deploy-infra                          # Azure (default)
make deploy-infra TARGET_CLOUD=gcp         # GCP
make deploy-infra TARGET_CLOUD=aws         # AWS

# Deploy das aplicações
make deploy-all                            # Azure (default)
make deploy-all TARGET_CLOUD=gcp           # GCP
make deploy-all TARGET_CLOUD=aws           # AWS
```

### Diretamente via Script

```bash
# Azure
bash infra/azure/deploy_infra.sh
bash infra/azure/deploy_app.sh

# GCP
bash infra/gcp/deploy_infra.sh
bash infra/gcp/deploy_app.sh

# AWS
bash infra/aws/deploy_infra.sh
bash infra/aws/deploy_app.sh
```

## Pré-requisitos por Cloud

| Cloud   | CLI Necessária | Autenticação           |
|---------|----------------|------------------------|
| Azure   | `az`           | `az login`             |
| GCP     | `gcloud`       | `gcloud auth login`    |
| AWS     | `aws`          | `aws configure`        |

## Tier FinOps (Custo Mínimo)

Todos os scripts utilizam os tiers mais econômicos disponíveis:

- **Azure**: PostgreSQL Burstable B1ms (~R$60/mês) + Redis Basic C0 (~R$70/mês)
- **GCP**: Cloud SQL f1-micro (Free Tier elegível) + Memorystore Basic 1GB (~US$35/mês)
- **AWS**: RDS t3.micro (Free Tier elegível) + ElastiCache t3.micro (Free Tier elegível)
