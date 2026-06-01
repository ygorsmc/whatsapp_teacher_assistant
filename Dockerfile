# Usa uma imagem oficial do Python, versão slim para reduzir o tamanho (ideal para containers na nuvem)
FROM python:3.11-slim

# Define variáveis de ambiente
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Define o diretório de trabalho
WORKDIR /app

# Instala dependências do sistema necessárias (ex: para compilar pacotes C, se houver)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala as dependências do Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia o código-fonte para o container
COPY ./src /app/src

# Expõe a porta 8000 (necessário apenas para o Webhook/FastAPI)
EXPOSE 8000

# Cria um usuário não-root por questões de segurança
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# O comando padrão iniciará o webhook. 
# Para rodar o worker no Azure Container Apps, basta sobrescrever o comando na definição do container:
# command: ["python", "-m", "src.workers.llm_worker"]
CMD ["uvicorn", "src.webhook.main:app", "--host", "0.0.0.0", "--port", "8000"]
