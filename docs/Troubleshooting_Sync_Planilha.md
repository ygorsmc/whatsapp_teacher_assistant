# 🛠️ Troubleshooting: Sincronização da Planilha (`/sinc_alunos`)

Este documento serve como um guia técnico para qualquer engenheiro de software que venha a atuar no **Assistente WhatsApp do Professor**. O foco aqui é explicar a arquitetura, o fluxo de execução e as armadilhas comuns encontradas no processo de sincronização de dados da planilha Google Sheets para o banco de dados em nuvem.

---

## 🌍 O Contexto e o Ambiente

A aplicação possui um comando chamado `/sinc_alunos`. Quando o professor digita isso no Telegram/WhatsApp, o sistema lê uma planilha do Google Sheets, faz o parsing das abas de "Professores", "Alunos" e "Turmas", e realiza a ingestão (Upsert) desses registros no Banco de Dados Relacional.

### A Transição de Banco de Dados (A "Armadilha")
O grande desafio enfrentado nesta etapa ocorreu devido à **Migração de Dialeto SQL**:
- **Ambiente Local (Testes Iniciais):** O sistema foi desenhado inicialmente utilizando **SQLite** (`sqlite+aiosqlite:///`). O SQLite é conhecido por ter "tipagem fraca" e aceitar comandos permissivos (ex: inserir inteiros em campos booleanos ou sintaxes agnósticas como `INSERT OR IGNORE`).
- **Ambiente de Produção (Nuvem Azure):** O sistema em produção utiliza o **PostgreSQL** hospedado na Azure, acessado via driver `asyncpg` (`postgresql+asyncpg://`). O PostgreSQL aplica validação estrita de tipos (Strict Typing) e possui um dialeto SQL próprio.

---

## 📂 Arquivos Envolvidos no Fluxo

O caminho de um comando `/sinc_alunos` passa pela seguinte arquitetura:

1. **`src/webhook/handlers.py`**: A porta de entrada. Recebe a requisição (POST) da Meta/Telegram. Registra uma "trava" (lock) idempotente no Redis para evitar requisições duplicadas e enfileira a mensagem.
2. **`src/workers/command_worker.py`**: O Worker LLM em background coleta a mensagem da fila. Identifica o comando `/sinc_alunos` e aciona a função `run_sync_alunos_command()`.
3. **`src/drive/spreadsheet_sync.py`** (O Coração do Processo): Faz a comunicação via API com o Google Sheets, processa as linhas lidas e monta as queries SQL transacionais que realizam o *Upsert* (Insert/Update) nas tabelas do Banco de Dados.
4. **`src/db/database.py`**: Gerencia a conexão (Async Engine e SessionLocal) usando SQLAlchemy.
5. **`test_sync.py`**: Um script na raiz que utilizamos como "Scratch/Test" para invocar a função `sync_users_from_sheet()` isoladamente direto contra o PostgreSQL localmente, permitindo visualizar os traces de erro ocultos.

---

## ⚙️ A Lógica da Sincronização

A lógica do arquivo `spreadsheet_sync.py` é feita em "Passes" (Fases) transacionais:
1. **Passo 1 (Upsert Teachers):** Cadastra professores primeiro, para garantir que as "Chaves Estrangeiras" (Foreign Keys) existam quando for cadastrar os cursos.
2. **Passo 2 (Upsert Students & Courses):** Varre a aba principal para inserir o Aluno, o Curso que ele comprou, a Turma na qual ele se matriculou e vincula o horário na tabela `StudentSchedule`.
3. **Passo 3 (Upsert Calendar):** Sincroniza a aba "Turmas" (cronograma global das turmas).
4. **Passo 4 (Limpeza):** Deleta agendamentos "órfãos" que não constam mais na planilha atual.

Todas as transações SQL utilizam sintaxe parametrizada segura (`:id`, `:name`, etc.) para evitar SQL Injection.

---

## 🐛 Casos de Uso Reais de Debugging (Bugs Encontrados)

Durante a subida do código nativo de SQLite para PostgreSQL na Azure, diversos `Crashes` ocorreram e exigiram correção. Aqui estão os principais e suas causas-raiz:

### Erro 1: `asyncpg.exceptions.DataError: invalid input syntax for type boolean`
- **O Sintoma:** O worker falhava no meio da execução e engolia o erro, devolvendo ao usuário apenas um aviso genérico de "Erro Inesperado".
- **O Culpado:** No SQL do SQLite, os campos booleanos estavam sendo inseridos via literais inteiros (ex: `VALUES (:id, :name, 1)`). O PostgreSQL rejeitava esse input.
- **A Solução Resolvida:** Vasculhamos o `spreadsheet_sync.py` e substituímos todos os `1` por `true` nativo de SQL.

### Erro 2: Avaliação de Python com fallback incorreto
- **O Sintoma:** O mesmo `DataError` persistia ao lidar com valores ausentes na coluna "Status" do Professor.
- **O Culpado:** O código Python tinha uma expressão ternária falha: `is_active = true if status_val in ("ativo") else 0`. Primeiro: a palavra minúscula `true` causaria um NameError em Python se não fosse engolida por try/excepts externos, e o retorno `0` caía na regra estrita do PostgreSQL.
- **A Solução Resolvida:** Corrigimos para `is_active = True if ... else False`.

### Erro 3: `asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "OR"`
- **O Sintoma:** Com as tabelas tipadas corretamente, a falha ocorreu no banco acusando erro de sintaxe.
- **O Culpado:** Uso agressivo da sintaxe `INSERT OR IGNORE` e de funções nativas do motor relacional como `DATE('now', 'localtime')`. O PostgreSQL não suporta nenhum dos dois nativamente dessa forma.
- **A Solução Resolvida:** Fizemos replace massivo de `INSERT OR IGNORE` para a sintaxe do PostgreSQL: `INSERT INTO ... ON CONFLICT DO NOTHING`. E também substituímos a função `DATE()` por `CURRENT_DATE`.

---

## 🔒 Mecanismos de Resiliência (Fallback e Retry)

Este ecossistema já possui defesas projetadas para a nuvem:
* **Tenacity Retries:** O wrapper do Redis foi adaptado com a flag nativa `retry_on_timeout=True` e `health_check_interval=30` no `redis_client.py` para não abortar requisições do webhook no meio de instabilidades corriqueiras de Container Apps (quando o *Socket Connection Lost* acontece).
* **Idempotência no Webhook:** Quando ocorre um "Soluço", o Webhook cria um Lock (fechadura temporária com expiração de 300s no Redis) e impede que tentativas insistentes do aplicativo de mensagem causem múltiplas sincronizações em paralelo.
* **Fallback Local:** Em caso do Redis despencar por completo, ele provê um InMemory dict-based que garante a continuação do roteamento.

> [!TIP]
> Caso a sincronização pare de responder de forma crônica no futuro, o ideal não é olhar o Log do Webhook, mas sim instanciar o Python local, ativar o PostgreSQL `.env` e rodar um script temporário como `test_sync.py` para visualizar o StackTrace real (o Command Worker está propositalmente programado para não vazar StackTrace para o usuário de Telegram).
