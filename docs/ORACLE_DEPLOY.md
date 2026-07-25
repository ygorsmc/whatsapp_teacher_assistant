# Deploy single-VM na Oracle Cloud Always Free (ARM A1)

Roda o stack inteiro numa VM ARM Ampere A1 (**4 OCPU / 24 GB**), sem nada pago:
webhook + workers + Postgres + Redis + Qdrant + Ollama (embeddings), exposto por
**Cloudflare Tunnel**. Comparado com o footprint always-on da prod no Azure
(~2 vCPU, ~5 GB), sobra folga.

Canal ativo por padrão: **Telegram**. O WhatsApp fica com o caminho pronto — é só
config (mesmo webhook, mesma VM), sem mudar infra.

Arquivos deste deploy:
- [`docker-compose.oracle.yml`](../docker-compose.oracle.yml) — o stack unificado (borda = `cloudflared`)
- [`.env.oracle.example`](../.env.oracle.example) — segredos/config
- [`scripts/oracle_create_instance.sh`](../scripts/oracle_create_instance.sh) — criação da VM com retry
- [`docker/caddy/Caddyfile`](../docker/caddy/Caddyfile) — só se usar o fallback `--profile caddy`

---

## 0. As 3 pegadinhas do free tier (leia antes)

1. **Conseguir alocar a A1 é a parte difícil, não rodar.** A cota ARM vive
   esgotada nas regiões populares → erro `Out of host capacity`. O script de
   criação re-tenta em loop entre os availability domains; deixe rodando.
2. **Idle reclaim.** Conta trial recupera VM ociosa (CPU **e** rede **e** memória
   < 10% no p95 de 7 dias). Aqui o Postgres+Ollama já estouram o critério de
   memória sozinhos, mas o jeito 100% garantido é **fazer upgrade pra Pay-As-You-Go**:
   continua **grátis** dentro dos limites Always Free, remove o idle reclaim e
   ainda dá **prioridade de capacidade** (resolve a pegadinha #1).
3. **Sem GPU.** O reranker GPU do dev (`docker/reranker`) não entra. Fica
   `RERANKER_PROVIDER=none` (ou `cohere` managed). Não é bloqueio.

---

## 1. Criar a VM

Pré: OCI CLI autenticado (ou use o **OCI Cloud Shell**, já vem pronto) e uma
**VCN com subnet pública** (wizard "VCN with Internet Connectivity").

```bash
export OCI_COMPARTMENT_ID=ocid1.tenancy.oc1..xxxx   # a tenancy raiz serve
export OCI_SUBNET_ID=ocid1.subnet.oc1..xxxx
export OCI_SSH_KEY=$HOME/.ssh/id_rsa.pub
./scripts/oracle_create_instance.sh
```

O `cloud-init` embutido instala Docker + plugin compose. Quando sair "VM criada e
RUNNING", anote o **IP público** (só pro SSH).

> Com o Cloudflare Tunnel você **NÃO** precisa abrir 80/443 na VCN security list
> nem no iptables — o túnel é saída-only. (O cloud-init até abre as portas, mas
> isso só importa no fallback `--profile caddy`.)

## 2. Criar o Cloudflare Tunnel

No **Cloudflare Zero Trust** → Networks → Tunnels → *Create a tunnel* (tipo
`cloudflared`):
1. Dê um nome, copie o **token** do tunnel (o `docker run ... --token XXXX`; você
   só precisa do `XXXX`) → vai em `TUNNEL_TOKEN` no `.env.oracle`.
2. Aba **Public Hostname**: crie um hostname (ex.: `bot.seudominio.com`, num
   domínio que você tenha na Cloudflare) → **Service** = `HTTP` → `webhook:80`.
   Como o `cloudflared` roda na mesma rede do compose, ele resolve `webhook:80`.

Esse hostname é a base pública do webhook (estável — não muda em restart).

## 3. Subir o stack

```bash
ssh ubuntu@<ip>
git clone <seu-repo> app && cd app          # ou rsync do seu código

cp .env.oracle.example .env.oracle
nano .env.oracle                            # preencha TUNNEL_TOKEN, Telegram, senha do PG...
cp /caminho/service_account.json .          # se usar Drive/Sheets (gdrive)

docker compose -f docker-compose.oracle.yml up -d
docker compose -f docker-compose.oracle.yml logs -f
```

Ordem de boot automática: `postgres` saudável → `migrate` (alembic) sai 0 →
webhook + workers + `cloudflared` sobem. O `ollama-pull` baixa o bge-m3 (~1.2 GB)
em paralelo; a primeira busca RAG espera esse download terminar.

Ingestão de material do Drive (não fica no ar o tempo todo):
```bash
docker compose -f docker-compose.oracle.yml --profile ingest up -d
```

## 4. Registrar o webhook do Telegram

Aponte o bot pro seu hostname do túnel (rode de qualquer lugar):
```bash
export TELEGRAM_BOT_TOKEN=...            # o mesmo do .env.oracle
export TELEGRAM_WEBHOOK_SECRET=...       # idem
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=https://SEU_HOSTNAME/v1/webhook/telegram" \
  --data-urlencode "secret_token=${TELEGRAM_WEBHOOK_SECRET}"

# conferir:
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```
`"ok": true` e `url` batendo = pronto. Mande uma mensagem pro bot e acompanhe
`logs -f worker-llm`.

## 5. Ligar o WhatsApp depois (caminho pronto)

Sem tocar em infra — mesma VM, mesmo túnel, mesmo webhook:
1. No `.env.oracle`: `CHANNEL_BACKEND=whatsapp`, `SCHEDULER_MODE=template` e
   preencha o bloco WhatsApp (token permanente / System User, `META_APP_SECRET`,
   `META_VERIFY_TOKEN`, `PHONE_NUMBER_ID`).
2. Adicione um **Public Hostname** no mesmo tunnel (ou reuse o mesmo) → o webhook
   já responde `/v1/webhook/whatsapp`.
3. Na Meta (WhatsApp → Configuration → Webhook):
   - **Callback URL**: `https://SEU_HOSTNAME/v1/webhook/whatsapp`
   - **Verify token**: o `META_VERIFY_TOKEN`
   - Assine o campo `messages`.
4. `docker compose -f docker-compose.oracle.yml up -d` (recria os containers com o
   novo canal).

> Dá pra rodar os dois canais ao mesmo tempo? O `CHANNEL_BACKEND` define o canal
> padrão do **scheduler** (mensagens automáticas). As duas rotas de entrada
> (`/telegram` e `/whatsapp`) ficam sempre ativas; o que muda é por qual canal o
> bot dispara as mensagens proativas.

---

## Operação

| Ação | Comando |
|---|---|
| Status | `docker compose -f docker-compose.oracle.yml ps` |
| Logs do scheduler | `docker compose -f docker-compose.oracle.yml logs -f worker-scheduler` |
| Logs do túnel | `docker compose -f docker-compose.oracle.yml logs -f cloudflared` |
| Re-migrar DB | `docker compose -f docker-compose.oracle.yml run --rm migrate` |
| Reiniciar 1 serviço | `docker compose -f docker-compose.oracle.yml restart worker-llm` |
| Backup do Postgres | `docker compose -f docker-compose.oracle.yml exec postgres pg_dump -U whatsapp whatsapp_assistant > backup.sql` |

## Notas de RAG / parsing

- **Embeddings**: locais no Ollama (`bge-m3`, 1024d). `EMBEDDING_DIMENSIONS=1024`
  tem que casar com `QDRANT_COLLECTION` — se reusar coleção antiga de 1536d, a
  busca quebra. Comece com coleção nova (`documents_bge1024`).
- **Parsing na ingestão**: default `PARSER_BACKEND=mistral` (Mistral OCR pro texto
  + Gemini Vision pra imagens) — precisa de `MISTRAL_API_KEY` + a `GOOGLE_API_KEY`.
  Pra só **rodar** o bot sobre material já ingerido, isso não importa.
- **Reranker**: `none` funciona liso. Pra ganhar precisão sem GPU, ligue
  `RERANKER_PROVIDER=cohere` + `COHERE_API_KEY` (managed, barato com `rerank-v4.0-fast`).
