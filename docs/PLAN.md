# Plano de Atualização da Documentação Arquitetural

**Objetivo:** Refletir na documentação oficial (`Estrutura do Projeto` e `Resumo Arquitetural`) as estabilizações e mudanças reais implementadas na arquitetura do WhatsApp Teacher Assistant após os testes em produção.

## 🔴 User Review Required

> [!IMPORTANT]
> **Aprovação Necessária**
> Por favor, revise este plano de documentação. Ao aprovar, iniciaremos a **Fase 2 (Implementação)** disparando os agentes especializados em documentação e backend para consolidar essas informações nos arquivos Markdown oficiais.

## 🔒 Mudanças Arquiteturais Consolidadas (A Serem Documentadas)

1. **Unificação dos Workers (`worker-llm` e `worker-command`)**
   * *O que mudou:* O container `worker-command` foi desativado e integrado ao `worker-llm` para otimização de recursos computacionais e simplificação da infraestrutura no Azure Container Apps. O `worker-llm` agora executa tanto o loop de mensagens padrão (via fila `inbound_messages`) quanto o loop de comandos administrativos (via fila `command_messages`) através de concorrência com `asyncio.gather`.
2. **Otimização Extrema do Webhook (< 50ms)**
   * *O que mudou:* O webhook agora utiliza um Cache em memória (`self.senders`) para a conexão TCP/AMQP do Service Bus e um hard-timeout configurado em 1.0 segundo para o Redis. Isso garante que o Webhook nunca sofra o timeout implacável da Meta (que causava um efeito dominó de 5 retries com `msg_id`s diferentes burlando o lock de idempotência).
3. **Conversão de Datas para Azure SQL Server (`DATETIME`)**
   * *O que mudou:* Ajuste na camada de ingestão (`spreadsheet_sync.py`) abandonando o formato longo ISO-8601 (com timezone `+00:00`) e padronizando o formato `%Y-%m-%dT%H:%M:%S` para compatibilidade com colunas `DATETIME` do SQL Server. Isso evitou a quebra silenciosa da inicialização dos alunos via comando `/sinc_alunos`.
4. **Substituição do Azure Cache for Redis por um Container App (Redis)**
   * *O que mudou:* Em vez de utilizar o serviço gerenciado (e mais caro) "Azure Cache for Redis", implantamos a imagem oficial `redis:7-alpine` como um Container App interno no mesmo ambiente (VNet). Isso reduz custos massivamente enquanto mantém o ecossistema Serverless (Container Apps).

---

## 🛠️ Proposed Changes (Fase 2)

### 1. Atualização do Resumo Arquitetural (`documentation-writer`)
*   **[MODIFY]** `Resumo Arquitetural - Assistente WhatsApp.md`
    *   Remover a menção ao `[Command Handler]` como um worker isolado no diagrama visual, indicando que ele é um loop interno do `[Worker LLM]`.
    *   Adicionar ênfase na configuração de Timeout do Redis (1.0s) como decisão crítica de engenharia para sobrevivência do Webhook perante a agressividade da Meta.
    *   Especificar a compatibilidade nativa de datas do banco Azure SQL Server.
    *   Atualizar a tabela de Stack Tecnológica: alterar "Azure Cache for Redis" para "Redis (Container App Interno)".

### 2. Atualização da Estrutura do Projeto (`backend-specialist`)
*   **[MODIFY]** `Estrutura do Projeto - Assistente WhatsApp.md`
    *   Atualizar o mapa hierárquico removendo o serviço de comandos em um container separado.
    *   Detalhar a mecânica de cache do `ServiceBusSender` acoplado no Singleton `ServiceBusWrapper` como parte integrante da estrutura de rede interna (`infra`).

---

## ✅ Verification Plan

### Validação
- Leitura final dos arquivos Markdown renderizados para garantir formatação limpa e diagramas legíveis.
- Verificação cruzada com o estado real do código em `src/workers` e `src/webhook`.
