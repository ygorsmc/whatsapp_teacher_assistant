# Plano de Preenchimento de Variáveis `.env.prod`

Este plano organiza as tecnologias que necessitam de cadastro para preencher as credenciais de produção no arquivo `.env.prod`. Ele as agrupa por contexto para que as escolhas fiquem evidentes, reduzindo o esforço do cadastro.

## 1. Mapeamento das Tecnologias por Vertical

### A. Vertical de Idiomas (Speech Services)
As credenciais desta seção alimentam Text-to-Speech (TTS), Speech-to-Text (STT) e Avaliação de Pronúncia. O projeto é provider-agnóstico, logo **você não precisa de todas**.

- **Azure AI Speech (Mais Recomendado)**
  - Cobre TTS, STT e Avaliação de Pronúncia.
  - Variáveis: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` (já preenchida com `brazilsouth`).
- **Google Cloud Speech (Alternativa TTS/STT)**
  - Variáveis: `GOOGLE_TTS_API_KEY`, `GOOGLE_STT_API_KEY`.
- **ElevenLabs (Alternativa TTS)**
  - Variável: `ELEVENLABS_API_KEY`.
- **Whisper (Alternativa STT)**
  - Variáveis: `WHISPER_ENDPOINT`, `WHISPER_API_KEY`.

### B. Vertical RAG de Alta Precisão (Recuperação Avançada)
Ferramentas usadas nas fases F0 a F8 para aprimorar o contexto.

- **Provedores de Reranking (Escolher apenas UM)**
  - **Cohere**: Necessita conta no Cohere. Variável: `COHERE_API_KEY`.
  - **Voyage AI**: Necessita conta no Voyage. Variável: `VOYAGE_API_KEY`.
  - **Open Reranker (Self-Hosted)**: Variáveis `OPEN_RERANKER_ENDPOINT` e `OPEN_RERANKER_API_KEY`.
- **Embedder Híbrido (BGE-M3)**
  - É necessário um endpoint de inferência (como RunPod ou HF Inference Endpoints).
  - Variáveis: `BGE_M3_ENDPOINT`, `BGE_M3_API_KEY`.

### C. Vertical de Exatas (Marker / Rendering)
Ferramentas para parsing de matemática/fórmulas em PDFs e geração de imagens das fórmulas.

- **Marker (Parser OCR)**
  - Precisa ser self-hosted (uma máquina em nuvem dedicada para rodar o modelo de visão).
  - Variáveis: `MARKER_ENDPOINT`, `MARKER_API_KEY`.
- **Math Renderer (Endpoints)**
  - Variáveis: `MATH_RENDER_ENDPOINT`, `MATH_RENDER_API_KEY`. *(Apenas se o `MATH_RENDERER=endpoint` for selecionado em vez do default `matplotlib` já embutido no Python).*

---

## 2. Passo a Passo do Cadastro (Ordem Sugerida)

### Fase 1: Decisões Arquiteturais e Exclusões Mútuas
1. **Decisão de Voz**: Decida se adotaremos Azure para a vertical de Idiomas inteira. Isso elimina a necessidade de cadastrar ElevenLabs, Google e Whisper local.
2. **Decisão de Reranker**: Escolha entre Cohere, Voyage ou Open Reranker. Geralmente Cohere (free tier ou fast tier) é o mais simples de se começar. O resto pode ficar vazio (`""`).
3. **Decisão de Renderizador Matemático**: Manter `CHART_RENDERER=matplotlib` não exige cadastro. Se mantermos assim, ignoramos as chaves de `MATH_RENDER_*`.

### Fase 2: Serviços em Nuvem Gerenciados (Fáceis)
1. **Azure Speech Services**:
   - Cadastrar conta Azure e criar um recurso "Speech Services" no "brazilsouth".
   - Copiar a key 1 e colar em `AZURE_SPEECH_KEY`.
2. **Provedor de Rerank (Ex: Cohere)**:
   - Cadastrar no Cohere Dashboard.
   - Criar uma trial API key e preencher `COHERE_API_KEY`.

### Fase 3: Serviços Self-Hosted (Exigem infraestrutura própria)
1. **Marker OCR Endpoint**:
   - Subir uma VM (na AWS, Azure, ou RunPod) rodando a API do Marker via Docker.
   - Gerar um token local para proteger a API.
   - Preencher `MARKER_ENDPOINT` e `MARKER_API_KEY`.
2. **BGE-M3 Embedder**:
   - Caso for usar a busca híbrida (M4 / F3), subir o TEI (Text Embeddings Inference) em uma VM ou serviço de container.
   - Preencher `BGE_M3_ENDPOINT` e `BGE_M3_API_KEY`.

---

## Próximos Passos (Ação do Usuário)
- Confirme se você concorda com este plano, indicando quais provedores (Azure, Cohere, ElevenLabs, etc.) você gostaria de focar primeiro, para eu focar apenas nos que realmente serão ativados.
