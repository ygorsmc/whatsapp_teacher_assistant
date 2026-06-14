"""
Constant strings for messaging interface (UX Copy).
These constants decouple user-facing text from business logic.
"""

# ==========================================
# REVIEW & SCHEDULER UX
# ==========================================
REVIEW_INVITE_BODY = "📚 *Revisão Diária — {course_name}*\n\nOlá {student_name}! Preparei uma revisão dos pontos-chave da sua última aula. Vamos revisar?\n\n🔓 *Importante:* Clique em qualquer botão abaixo para interagir. Deixar para depois não tem problema: pula apenas esta revisão, mas garante que as próximas cheguem direto para você!"
REVIEW_INVITE_FOOTER = "Revisão rápida + quiz de fixação"

REVIEW_ACCEPT_TITLE = "✅ Sim, vamos lá!"
REVIEW_LATER_TITLE = "⏰ Depois"
REVIEW_LATER_ACK = "Sem problemas! Me chame quando tiver um tempinho livre para revisarmos."

# ==========================================
# QUIZ UX
# ==========================================
QUIZ_BODY_PREFIX = "📝 *Quiz de Revisão*\n\n"
QUIZ_FOOTER_BUTTONS = "Clique na resposta correta"
QUIZ_FOOTER_LIST = "Selecione a resposta correta"
QUIZ_LIST_BUTTON_TEXT = "Ver opções"
QUIZ_LIST_SECTION_TITLE = "Alternativas"

FEEDBACK_CORRECT = "🎉 Correto! {explanation}"
FEEDBACK_INCORRECT = "❌ Incorreto. A resposta certa era a alternativa {correct_answer}. \n\n{explanation}"

# ==========================================
# ADDON DE LIVROS — JORNADA DE LEITURA UX
# ==========================================
# Microdose de leitura: fatia de 10-15 min, NUNCA "leia o capítulo inteiro".
READING_SUGGESTION_BODY = "📖 *Leitura de hoje — {material_label}*\n\nOlá {student_name}! Sua próxima microdose de leitura está pronta:\n\n👉 *{section_label}*\n⏱️ Reserve uns 10-15 minutinhos — se a seção for longa, leia até onde der com atenção.\n\nÀs {checkin_hour} eu mando um mini-quiz de check-in para destravar a próxima etapa. 😉\n\n{progress_line}"

# Progresso visível em TODA interação de leitura (Fase 6.4).
READING_PROGRESS_LINE = "📖 Você está em {percent}% do {material_label} | {streak_days} dia(s) seguidos de estudo 🔥"

# Replanejamento SEM CULPA: nunca dizer "você está X capítulos atrasado".
READING_RECALC_BODY = "📖 *{material_label}*\n\nOi {student_name}! Percebi que a última leitura ficou para depois — acontece com todo mundo! 🤗\n\nQuer que eu ajuste seu plano de leitura para o novo ritmo?"
READING_RECALC_YES_TITLE = "✅ Ajustar plano"
READING_RECALC_KEEP_TITLE = "▶️ Manter como está"
READING_RECALC_DONE = "Prontinho! 🎯 Plano recalculado a partir de hoje, sem pular nada e sem culpa. Sua próxima microdose chega no horário de sempre. 💪"
READING_RECALC_KEPT = "Combinado! Seguimos no plano atual. Sua próxima leitura chega no horário de sempre. 📚"

READING_PAUSED = "🏖️ *Modo férias ativado!*\n\nSua jornada de leitura está pausada — sem cobranças e sem perder seu progresso. Quando voltar, é só mandar /retomar_leitura que eu recalculo tudo sem penalidades. Boas férias!"
READING_RESUMED = "🎉 *Bem-vindo(a) de volta!*\n\nSua jornada de leitura foi retomada e o plano recalculado a partir de hoje — você continua exatamente de onde parou ({progress_line}).\n\nA próxima microdose chega no horário de sempre!"
READING_NO_PLAN = "📚 Você ainda não tem um plano de leitura ativo. Peça ao seu professor para criar com /plano_leitura!"

READING_CHECKIN_INTRO = "🔔 *Check-in de leitura — {section_label}*\n\nHora de validar a leitura de hoje! Responda o mini-quiz abaixo para desbloquear a próxima etapa. 👇"
READING_GATE_PASSED = "🔓 *Etapa desbloqueada!*\n\n{progress_line}\n\nSua próxima microdose chega no horário de sempre. Continue assim! 🚀"
READING_GATE_RETRY = "💪 Sem problemas — errar faz parte! Que tal *reler o {section_label}*? É rapidinho (10-15 min). Eu repito a leitura no seu plano e mando um novo check-in na próxima rodada. Você consegue!"
READING_COMPLETED = "🏆 *PARABÉNS, {student_name}!*\n\nVocê concluiu 100% do {material_label}! 🎉🎉\n\nQue jornada! Seu plano foi encerrado, mas as revisões espaçadas continuam chegando para fixar tudo na memória de longo prazo. 🧠"

# Revisão Espaçada (Fase 4)
SPACED_REVIEW_INTRO = "🧠 *Revisão Espaçada*\n\nLembra deste conteúdo? Revisitar no momento certo é o segredo da memória de longo prazo. Responda aí! 👇"

# Mapeamento Aula ↔ Capítulo (Fase 6.5)
LESSON_CHAPTER_SUGGESTION = "\n\n📚 *Dica:* o conteúdo de hoje complementa o *Capítulo {chapter}* do seu livro. Que tal revisar?"

# Errata Viva (Fase 6.6)
ERRATA_WARNING = "\n\n⚠️ *Atenção — Errata desta seção:*\n{correction}"

# Proteção de conteúdo (rate limit RAG sobre o mesmo material)
READING_RATE_LIMITED = "📚 Calma, maratonista! Para proteger o conteúdo do material, eu respondo no máximo *3 consultas por minuto* sobre o livro/apostila. Respira, relê o trecho, e me pergunta de novo em instantes. 😉"

# Simulado (Fase 6.3 / H2.3 cronometrado)
SIMULADO_STUDENT_INTRO = "🎯 *SIMULADO — {scope_label}*\n\nSeu professor montou um simulado cruzando os capítulos/tópicos já estudados (até o capítulo {max_section}). São {count} questões — responda uma a uma com calma. Boa prova! 🍀"
SIMULADO_TIMED_NOTE = "\n⏰ *Atenção:* o simulado fecha em {hours}h. Depois disso você recebe seu resultado com o comparativo (anônimo) da turma!"
SIMULADO_RESULT_PERCENTILE = "🏁 *Resultado do Simulado*\n\nVocê acertou *{correct}/{total}* — à frente de *{percentile}%* da turma. 📊\n\n_Os erros viraram revisões espaçadas: eles voltam para você nos próximos dias até a memória fixar. 🧠_"
SIMULADO_RESULT_SMALL_COHORT = "🏁 *Resultado do Simulado*\n\nVocê acertou *{correct}/{total}*! 💪\n\n_Os erros viraram revisões espaçadas — eles voltam para você nos próximos dias. 🧠_"

# ==========================================
# H1.1 — DESAFIO DE TURMA (placar anônimo)
# ==========================================
CHALLENGE_STUDENT_INTRO = "🏆 *DESAFIO DA TURMA — {class_name}*\n\nTodo mundo recebeu a MESMA questão. Você tem *{hours}h* para responder — depois eu conto como a turma foi (sem expor ninguém 😉). Valendo! 👇"
CHALLENGE_RESULT_WINNER = "🏆 *Placar do Desafio*\n\n*{percent}%* da turma acertou — e você foi um deles! 🏅 Continue assim!"
CHALLENGE_RESULT_MISSED = "🏆 *Placar do Desafio*\n\n*{percent}%* da turma acertou desta vez. Você errou junto com outros colegas — acontece! A explicação que você recebeu é o que importa. 💪"
CHALLENGE_RESULT_SMALL_COHORT = "🏆 *Desafio encerrado!*\n\nObrigado por participar — seu resultado individual já foi enviado com a explicação. 💪"

# ==========================================
# H1.4 — MEDALHAS DE STREAK
# ==========================================
STREAK_MEDAL_UNLOCKED = "\n\n{medal} *MEDALHA DESBLOQUEADA: {days} dias seguidos de estudo!* {medal}"

# ==========================================
# H2.1 — ATIVAÇÃO POR QR CODE (Livro Vivo)
# ==========================================
ACTIVATION_ASK_NAME = "📖 *Bem-vindo(a) ao {material_label} interativo!*\n\nSua chave *{key}* é válida! 🎉\n\nPara começar, me diga seu *nome* (é assim que vou te chamar).\n\n_🔒 Privacidade (LGPD): ao responder com seu nome, você concorda que seu número e seu progresso de leitura sejam usados apenas para entregar este serviço. Você pode sair a qualquer momento com /sair_livro._"
ACTIVATION_WELCOME = "🎉 *Tudo pronto, {student_name}!*\n\nSeu *{material_label}* agora é uma jornada guiada:\n📖 Microdoses de leitura de 10-15 min no seu WhatsApp\n🔓 Quiz de check-in que desbloqueia a próxima etapa\n🧠 Revisões espaçadas automáticas (D+3, D+10, D+30)\n\nSua primeira sugestão de leitura chega *amanhã às 07:00*. Quer começar agora? Digite */meu_progresso* 😉\n\n_Comandos: /meu_progresso · /flashcards · /pausar_leitura · /sair_livro_"
ACTIVATION_WELCOME_BACK = "🎉 *Novo material ativado, {student_name}!*\n\nSua jornada do *{material_label}* já está configurada — a primeira sugestão de leitura chega amanhã às 07:00.\n\n_Use /meu_progresso para ver todas as suas jornadas._"
ACTIVATION_KEY_INVALID = "❌ Não reconheci essa chave de ativação. Confira se digitou exatamente como está no seu material (ex: LIVRO-A1B2-C3D4) e tente de novo.\n\n_Se o problema continuar, fale com quem te forneceu o material._"
ACTIVATION_KEY_USED = "🔒 Essa chave já foi utilizada no número máximo de aparelhos permitido.\n\n_Se você acredita que é um engano, fale com quem te forneceu o material._"
ACTIVATION_KEY_REVOKED = "🚫 Essa chave foi desativada pelo fornecedor do material.\n\n_Entre em contato com quem te forneceu o material para receber uma nova._"
ACTIVATION_NAME_TOO_SHORT = "🤏 Esse nome ficou curtinho! Me diga seu nome (pelo menos 2 letras) para finalizarmos a ativação."
LEAVE_BOOK_CONFIRMED = "👋 *Pronto.* Sua jornada foi encerrada e seus dados pessoais foram removidos da base, como manda a LGPD.\n\nSe mudar de ideia, é só escanear o QR do seu material novamente (a chave continua sua). Bons estudos!"
LEAVE_BOOK_NOT_ALLOWED = "ℹ️ Sua conta é gerenciada pelo seu professor (matrícula de turma). Para sair, fale diretamente com ele."

# H3.1 — Onboarding self-service do professor
TEACHER_ACTIVATION_ASK_NAME = "👨‍🏫 *Bem-vindo(a), professor(a)!*\n\nSua chave *{key}* é válida! 🎉\n\nPara criar sua conta, me diga seu *nome completo*.\n\n_🔒 LGPD: ao responder, você concorda com o uso do seu número para operar o assistente._"
TEACHER_ACTIVATION_WELCOME = "🎉 *Conta criada, prof. {teacher_name}!*\n\nSeu assistente em 3 passos:\n\n1️⃣ *Material:* compartilhe sua pasta do Google Drive com:\n`{service_account_email}`\n(estrutura: Curso > Disciplina > Aulas/Livro > seções)\n\n2️⃣ *Turma:* `/criar_turma_MeuCurso_TurmaA`\n\n3️⃣ *Alunos:* `/adicionar_alunos_TurmaA_Maria:11999990001; João:11999990002`\n\nDepois é só rodar /sinc_materiais e a mágica começa. Digite /ajuda para ver tudo. 🚀"

# ==========================================
# H2.2 — CORREÇÃO DISSERTATIVA
# ==========================================
ESSAY_PROMPT_PHOTO = "📝 Beleza! Me envie agora a *foto da sua redação* (bem iluminada e sem cortes). Vou corrigir com os critérios do seu professor.\n\n_Você tem 10 minutos para enviar._"
ESSAY_RECEIVED = "📝 Recebi sua redação! Lendo e corrigindo com os critérios do professor... ⏳"
ESSAY_RATE_LIMITED = "📝 Você já usou suas {limit} correções de hoje. Amanhã o contador zera — aproveite para reescrever com o feedback que recebeu! 💪"
ESSAY_OCR_FAILED = "😕 Não consegui ler o texto da foto. Tente de novo com mais luz, sem sombra e com a folha inteira no enquadramento."
ESSAY_PHOTO_UNEXPECTED = "📷 Recebi sua foto! Se for uma *redação para corrigir*, envie /corrigir e depois a foto (ou mande a foto com a legenda \"corrigir\").\n\nPara dúvidas sobre a matéria, me mande sua pergunta por texto. 😉"

# ==========================================
# H1.2 — SENTINELA EMOCIONAL (alerta ao professor)
# ==========================================
SENTINEL_DIGEST_HEADER = "🛡️ *Sentinela — alunos que precisam de você hoje*\n"
SENTINEL_FOOTER = "\n_Uma mensagem sua de 30 segundos pode reverter um abandono. A Sentinela não repete o mesmo alerta por 7 dias._"

# ==========================================
# RAG DE ALTA PRECISÃO — RECUSA HONESTA (F5)
# ==========================================
# Sufixo da abstenção 🔴: anexado ao out_of_scope_message do professor quando
# o CRAG-lite não encontra evidência suficiente no material (o LLM gerador
# NEM é chamado — zero alucinação e zero custo de geração).
CRAG_ABSTENTION_SUFFIX = "\n\n🔎 Não encontrei isso no material da disciplina. Quer tentar reformular a pergunta com outras palavras? Já avisei seu professor para verificar se falta material sobre o assunto. 😉"

# Aviso HITL ao professor (fila outbound, dedupe diário por turma): falha de
# busca vira insumo de conteúdo — mesma filosofia do /corpus_duvidas.
CRAG_TEACHER_NOTICE = "📚 *Material com lacuna? — {class_name}*\n\nHoje, *{count}* pergunta(s) de aluno(s) não encontraram resposta no material indexado. Exemplos:\n{examples}\n\n_Vale subir um PDF sobre o assunto ou ajustar a aula? Perguntas sem resposta viram abstenção honesta — o assistente não inventa._"

# ==========================================
# LGPD — DIREITOS DOS TITULARES (Plano P1.1/P1.2/P1.6)
# ==========================================
# /apagar_meus_dados (Art. 18) — confirmação em 2 passos (evita exclusão acidental)
LGPD_DELETE_CONFIRM_PROMPT = "🗑️ *Apagar meus dados (LGPD)*\n\nIsso vai *encerrar sua conta* e remover seu nome, telefone e histórico de conversas dos nossos sistemas. Essa ação não pode ser desfeita.\n\nConfirma?"
LGPD_DELETE_DONE = "✅ Pronto. Seus dados pessoais foram removidos/anonimizados e você não receberá mais mensagens. Obrigado por ter estudado com a gente! 👋"
LGPD_DELETE_CANCELLED = "👍 Tudo certo, nada foi apagado. Seguimos estudando!"
LGPD_DELETE_FORWARDED = "📨 Recebi seu pedido! Como sua matrícula é gerida pela sua escola/professor, encaminhei a solicitação para o responsável pela turma concluir a exclusão. Você não receberá mais mensagens minhas até lá."
LGPD_DELETE_TEACHER_NOTICE = "🔐 *Pedido LGPD — exclusão de dados*\n\nO aluno *{student_name}* ({masked_contact}) pediu a exclusão dos dados dele (Art. 18). Como a matrícula é gerida por você/escola (controladora), confirme a exclusão removendo o aluno da planilha (Status: inativo) — a anonimização será concluída pela retenção automática.\n\n_Pedido registrado em DataSubjectRequests ({request_id})._"

# Consentimento do responsável (Art. 14) — termo em destaque + botões
GUARDIAN_CONSENT_REQUEST = "👋 Olá! Aqui é o assistente de estudos da turma *{class_name}*.\n\n*{student_first_name}* foi matriculado(a) pela escola e, por ser menor de 12 anos, a LGPD (Art. 14) exige a sua autorização como responsável antes de qualquer interação.\n\n📋 *Termo (versão {term_version}):* o assistente envia revisões e quizzes pelo WhatsApp/Telegram, guarda o progresso de estudo e NUNCA usa os dados para treinar IA ou para publicidade. Você pode revogar a qualquer momento respondendo *REVOGAR*.\n\nVocê autoriza?"
GUARDIAN_CONSENT_GRANTED_ACK = "✅ Autorização registrada — obrigado! {student_first_name} já pode estudar com o assistente. Para revogar no futuro, basta responder *REVOGAR*."
GUARDIAN_CONSENT_DENIED_ACK = "👍 Entendido — registramos a recusa e o assistente NÃO vai interagir com {student_first_name}. Se mudar de ideia, fale com a escola."
GUARDIAN_CONSENT_REVOKED_ACK = "✅ Revogação registrada. O assistente parou de interagir com o(s) aluno(s) sob sua responsabilidade. A escola foi o canal que cadastrou a matrícula — qualquer dúvida, fale com ela."
GUARDIAN_PENDING_STUDENT_MSG = "👋 Quase lá! Por você ser menor de idade, a lei (LGPD) pede a autorização do seu responsável antes de eu poder te ajudar. Já enviei o pedido — peça para ele(a) responder a mensagem! 😉"

# Revisão humana da correção de redação (Art. 20 — decisão automatizada)
ESSAY_REVIEW_FOOTER = "\n\n_🤖 Correção feita por IA com os critérios do professor. Quer uma revisão humana? Responda *REVISAR*._"
ESSAY_REVIEW_REQUESTED_ACK = "👨‍🏫 Combinado! Pedi para o professor revisar a correção da sua redação. Ele recebe o aviso ainda hoje."
ESSAY_REVIEW_TEACHER_NOTICE = "📝 *Pedido de revisão humana (LGPD Art. 20)*\n\nO aluno *{student_name}* pediu revisão da correção de redação feita pela IA na turma *{class_name}*. Vale dar uma olhada e responder a ele(a) direto. 😉"

# ==========================================
# ERRORS & VALIDATION UX
# ==========================================
ERROR_NO_MATERIAL = "Ainda não tenho materiais indexados para sua aula atual. Peça ao seu professor para sincronizar os arquivos. 📁"
ERROR_QUIZ_NOT_FOUND = "Este quiz não foi encontrado ou já expirou."
ERROR_QUIZ_ANSWERED = "Você já respondeu este quiz."
ERROR_UNAUTHORIZED = "Comando não autorizado."
ERROR_NOT_ENROLLED = "Sua conta não está ativa ou você não está matriculado neste curso. Entre em contato com o suporte."

# ==========================================
# COMMANDS UX (TEACHER ONLY)
# ==========================================
CMD_SYNC_ALUNOS_START = "⏳ Iniciando sincronização da planilha..."
CMD_SYNC_MATERIAIS_START = "⏳ Iniciando download e processamento de PDFs. O robô vai ler os arquivos e criar os vetores (Isso pode demorar alguns minutos)..."
CMD_SYNC_MATERIAIS_SUCCESS = "✅ *Ingestão Concluída!*\n\nOs materiais foram divididos, processados pela Inteligência Artificial e agora fazem parte do meu cérebro! 🧠\n\n📄 Documentos na base: {docs_before} ➡️ {docs_after}"
CMD_SYNC_SUCCESS = "✅ *Sincronização Concluída!*\n\n👨‍🏫 Professores: {teachers_before} ➡️ {teachers_after}\n🎓 Alunos: {students_before} ➡️ {students_after}\n📚 Cursos: {courses_before} ➡️ {courses_after}\n📘 Disciplinas: {subjects_before} ➡️ {subjects_after}\n⏰ Agendamentos: {schedules_before} ➡️ {schedules_after}"
CMD_SYNC_ERROR = "❌ Erro na sincronização: {error}"
CMD_SYNC_UNLINKED_WARNING = "\n\n⚠️ *{count} aluno(s) ainda não receberão quizzes* porque não vincularam a conta no bot:\n{names}\n\n_Nota: Isso só é necessário se o aluno for utilizar o Telegram. Se ele for usar apenas o WhatsApp, os quizzes chegarão normalmente._\nPeça para os alunos que queiram usar o Telegram abrirem o bot {bot}, mandarem uma mensagem e informarem o telefone."
CMD_SYNC_NO_TEACHER_WARNING = "\n\n⚠️ *{count} turma(s) sem professor* na aba 'Professores' (ficaram com o Admin):\n{names}\n\nAdicione cada uma na aba Professores (Curso + Turma) para que /quiz e /aula funcionem para o professor certo."
CMD_SYNC_DISCIPLINA_WARNING = "\n\n⚠️ *{count} turma(s) com disciplina divergente* entre as abas Alunos e Turmas (valeu a da aba Turmas):\n{names}\n\nUma turma deve ter UMA disciplina. Corrija a aba Alunos para bater com a aba Turmas."

TEACHER_CHAT_REPLY = """Olá Professor! 👋 Sou o seu assistente. O que gostaria de fazer?

Aqui estão os comandos disponíveis:
🔹 */sinc_alunos* - Atualiza alunos, cursos e horários com base na planilha.
🔹 */sinc_materiais* - Sincroniza os PDFs do Google Drive.
🔹 */ajuda* - Exibe a lista completa de comandos (inclui os relatórios gerenciais da turma).

Mensagens de conversação livre são destinadas apenas aos alunos."""

# ==========================================
# MANAGEMENT COMMANDS UX (TEACHER ONLY)
# ==========================================
CMD_HELP_TEXT = """📚 *Comandos disponíveis*

*Sincronização*
🔹 /sinc_alunos — Atualiza alunos, cursos e horários da planilha.
🔹 /sinc_materiais — Sincroniza os PDFs do Google Drive.
🔹 /listar_cursos — Lista seus cursos.
🔹 /listar_materiais — Status dos arquivos do Drive.

*Aula & Quiz*
🔹 /aula Turma = 2 — Define a aula atual da turma.
🔹 /quiz_Turma — Envia um quiz para a turma agora.

*Gestão da turma* (formato: /comando_Turma)
🔹 /engajamento_Turma — Alunos ativos e interações (7 dias).
🔹 /acertos_Turma — 3 tópicos com pior desempenho nos quizzes.
🔹 /risco_Turma — Alunos inativos ou com baixo aproveitamento.
🔹 /duvidas_frequentes_Turma — IA resume as dúvidas da semana.
🔹 /limpar_quizzes_Turma — Apaga o Banco de Questões da turma.
🔹 /aviso_Turma_Mensagem — Envia um aviso a todos os alunos.

*Gestão de aluno* (formato: /comando_Telefone)
🔹 /ver_aluno_11999998888 — Ficha do aluno (matrícula, aula, últimas interações).
🔹 /resetar_aluno_11999998888 — Reinicia a jornada individual do aluno.

*📖 Livros & Apostilas (Addon)*
🔹 /plano_leitura_Turma_diario — Cria a jornada de leitura (ritmos: diario | semanal).
🔹 /simulado_Turma_5 — Simulado com questões dos capítulos 1 a 5.
🔹 /vincular_aula_Turma_2_3 — Vincula a Aula 2 ao Capítulo 3 do livro.
🔹 /errata_Turma_cap3_Onde diz X, leia Y — Cadastra uma errata viva.
🔹 /ativar_capitulos_Turma_1,3,5 — Ativa só os capítulos do plano de curso.
🔹 /gabarito_Turma_cap3_ex2_42J — Cadastra gabarito oficial (âncora da IA).

*📊 Telemetria editorial*
🔹 /mapa_dificuldade_Turma — Heatmap de erros por capítulo/seção.
🔹 /funil_conclusao_Turma — Onde a turma "abandona" o livro.
🔹 /corpus_duvidas_Turma — Dúvidas agrupadas por capítulo (anonimizado).

*🏆 Engajamento & Correção*
🔹 /desafio_Turma — Mesma questão para todos + placar anônimo em 6h.
🔹 /rubrica_Turma_Critérios — Critérios oficiais da correção de redação.
🔹 /chaves_Turma — Status das chaves de ativação (QR) do material.

*🚀 Setup self-service*
🔹 /criar_turma_Curso_Turma — Cria curso e turma na hora.
🔹 /adicionar_alunos_Turma_Maria:11999990001; João:11999990002

_Comandos dos alunos: /pausar_leitura, /retomar_leitura, /meu_progresso, /flashcards, /corrigir (redação), /sair_livro_"""

CMD_CLASS_NOT_FOUND = "❌ Turma '{class_query}' não encontrada ou você não tem permissão sobre ela."
CMD_TEACHER_NOT_FOUND = "❌ Professor não encontrado no sistema."
CMD_AVISO_USAGE = "❌ Formato inválido. Use: `/aviso_Turma_Sua mensagem aqui`\n\nExemplo: `/aviso_TurmaA_Amanhã não tem aula!`"
CMD_STUDENT_PHONE_USAGE = "❌ Informe o telefone do aluno com DDD. Exemplo: `{command}_11999998888`"
CMD_STUDENT_NOT_FOUND = "❌ Nenhum aluno encontrado com o telefone terminando em {digits}."
CMD_STUDENT_NOT_OWNED = "🔒 Este aluno não está matriculado em nenhuma turma sua."

ERROR_MESSAGE_TOO_LONG = "Sua mensagem é muito longa. Por favor, seja mais conciso."
ERROR_TECHNICAL = "Enfrentei um problema técnico para gerar sua resposta. Por favor, tente novamente."
ERROR_INVALID_RESPONSE = "Resposta inválida."
UNSUPPORTED_MESSAGE_TYPE = "No momento, só consigo entender mensagens de texto e botões interativos."

# F3 (Vertical de Idiomas) — Speaking + Pronúncia
SPEAKING_RECEIVED = "🎙️ Recebi seu áudio! Analisando sua fala... ⏳"
SPEAKING_STT_FAILED = "😕 Não consegui entender o áudio. Tente de novo num lugar silencioso — ou me mande por texto."
SPEAKING_STT_UNAVAILABLE = "🎧 A análise de áudio está indisponível agora. Pode me mandar sua resposta por texto?"

# F5 (Vertical de Idiomas) — Nivelamento CEFR
PLACEMENT_UNAVAILABLE = "O teste de nivelamento não está disponível para a sua turma."
PLACEMENT_INTRO = "🎯 *Teste de nivelamento* — 6 perguntas rápidas, da mais fácil à mais difícil.\nResponda numa única mensagem com as letras, ex.: *1A 2B 3C 4A 5B 6C*\n"
PLACEMENT_FAILED = "😕 Não consegui montar o teste agora. Tente novamente em instantes."
PLACEMENT_ANSWERS_UNCLEAR = "Não entendi suas respostas. Envie as 6 letras, ex.: *1A 2B 3C 4A 5B 6C*"
PLACEMENT_RESULT = "🎉 Pronto! Seu nível estimado é *{cefr}*.\nVamos calibrar as próximas atividades para esse nível. Bons estudos! 💪"
