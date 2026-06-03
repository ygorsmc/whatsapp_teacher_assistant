"""
Constant strings for WhatsApp interface (UX Copy).
These constants decouple user-facing text from business logic.
"""

# ==========================================
# REVIEW & SCHEDULER UX
# ==========================================
REVIEW_INVITE_BODY = "📚 *Revisão Diária — {course_name}*\n\nOlá {student_name}! Preparei uma revisão dos pontos-chave da sua última aula. Vamos revisar?\n\n🔓 *Importante:* Sua janela de estudos de hoje está fechada! Clique em qualquer botão abaixo para abri-la. Deixar para depois não tem problema: pula apenas esta revisão, mas garante que as próximas cheguem direto para você!"
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
CMD_SYNC_SUCCESS = "✅ *Sincronização Concluída!*\n\n👨‍🏫 Professores: {teachers_before} ➡️ {teachers_after}\n🎓 Alunos: {students_before} ➡️ {students_after}\n📚 Cursos: {courses_before} ➡️ {courses_after}\n⏰ Agendamentos: {schedules_before} ➡️ {schedules_after}"
CMD_SYNC_ERROR = "❌ Erro na sincronização: {error}"

TEACHER_CHAT_REPLY = """Olá Professor! 👋 Sou o seu assistente. O que gostaria de fazer?

Aqui estão os comandos disponíveis:
🔹 */sinc_alunos* - Atualiza alunos, cursos e horários com base na planilha.
🔹 */sinc_materiais* - Sincroniza os PDFs do Google Drive.
🔹 */ajuda* - Exibe esta lista de comandos.

Mensagens de conversação livre são destinadas apenas aos alunos."""

ERROR_MESSAGE_TOO_LONG = "Sua mensagem é muito longa. Por favor, seja mais conciso."
ERROR_TECHNICAL = "Enfrentei um problema técnico para gerar sua resposta. Por favor, tente novamente."
ERROR_INVALID_RESPONSE = "Resposta inválida."
UNSUPPORTED_MESSAGE_TYPE = "No momento, só consigo entender mensagens de texto e botões interativos."
