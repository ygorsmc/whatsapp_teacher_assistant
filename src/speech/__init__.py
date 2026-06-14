"""
Vertical de Idiomas — camada de FALA (provider-agnóstica).

Três portas (Ports & Adapters), cada uma com factory + env var, exatamente como
`LLMProvider`, `IRerankerProvider` e `IDocumentParser`:

  - `ISpeechToText`         (STT)  → `STT_PROVIDER`
  - `IPronunciationAssessor`       → `PRONUNCIATION_PROVIDER`
  - `ITextToSpeech`         (TTS)  → `TTS_PROVIDER`

`none` é sempre um provider válido (degrada com graça): STT none = pede texto;
pronúncia none = só transcreve; TTS none/erro = texto. Nada aqui acopla a vertical
à Azure — trocar de fornecedor é trocar a env var.

Spec: `_docs/prompt_execucao_idiomas.md` (§1) · Doc: `_docs/docs/IDIOMAS_OPERATIONS.md`.
"""
