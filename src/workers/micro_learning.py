import json
from uuid import uuid4
from datetime import datetime, timezone, timedelta

import structlog
from src.db.database import AsyncSessionLocal
from sqlalchemy import text

from src.rag.quiz_generator import QuizOutput
from src.shared.constants.messages import (
    QUIZ_BODY_PREFIX,
    QUIZ_FOOTER_BUTTONS,
    QUIZ_FOOTER_LIST,
    QUIZ_LIST_BUTTON_TEXT,
    QUIZ_LIST_SECTION_TITLE,
    CLASS_REVIEW_SEPARATOR,
)

logger = structlog.get_logger(__name__)

async def send_quiz_to_student(channel, student: dict, quiz: QuizOutput, quiz_bank_id: str | None = None, reading_plan_id: str | None = None, challenge_id: str | None = None, target_language: str | None = None) -> str:
    """Salva o quiz no banco e envia ao aluno via mensagem interativa.

    Args:
        quiz_bank_id: id da questão de origem no QuizBank (Cache Semântico).
            Ao registrar a entrega com esse vínculo, garantimos que o aluno
            nunca receba a mesma dupla (Tópico + Lente) duas vezes e
            habilitamos a agregação por tópico do comando /acertos.
        reading_plan_id: quando preenchido, este quiz é o CHECK-IN da Jornada
            de Leitura (Addon de Livros): acertar desbloqueia a próxima seção
            do plano; errar oferece reler o trecho.
        challenge_id: quando preenchido, esta entrega participa de um Desafio
            de Turma/Simulado (ClassChallenges) — o fechamento agrega o placar
            anônimo por esta coluna.

    Retorna o quiz_id para rastreamento.
    """
    quiz_id = str(uuid4())[:8]  # ID curto para caber no reply_id do WhatsApp

    # Guard: quiz degenerado (o LLM às vezes devolve `alternatives` vazia e o
    # _shuffle_alternatives faz `return` silencioso, deixando passar). Sem isto,
    # persistíamos lixo no PendingQuizzes e o send_buttons/list disparava um 400
    # opaco da Meta — o resumo+áudio iam, mas o quiz "sumia". Falha cedo e clara.
    n_alts = len(quiz.alternatives or [])
    if n_alts < 2:
        raise ValueError(
            f"Quiz inválido: {n_alts} alternativa(s) (mínimo 2). "
            f"Pergunta: {quiz.question[:80]!r}"
        )

    # Persiste no banco para validação futura
    async with AsyncSessionLocal() as session:
        # Blindagem: quiz_bank_id é FK para QuizBank. Um id inválido (ex.: um
        # gerador que deixou passar um código alucinado do material) faria o
        # INSERT estourar por FK e derrubaria a entrega INTEIRA do quiz. Se o id
        # não existir no banco, degradamos para None em vez de quebrar.
        if quiz_bank_id:
            exists = await session.scalar(
                text('SELECT 1 FROM "QuizBank" WHERE id = :id'), {"id": quiz_bank_id}
            )
            if not exists:
                logger.warning("quiz_bank_id_invalido_ignorado", quiz_bank_id=quiz_bank_id)
                quiz_bank_id = None

        await session.execute(
            text("""
                INSERT INTO "PendingQuizzes"
                    (quiz_id, student_id, course_id, lesson_id, question_text,
                     alternatives, correct_answer, explanation, expires_at, status,
                     quiz_bank_id, reading_plan_id, challenge_id)
                VALUES
                    (:quiz_id, :student_id, :course_id, :lesson_id, :question,
                     :alternatives, :correct_answer, :explanation, :expires_at, 'pending',
                     :quiz_bank_id, :reading_plan_id, :challenge_id)
            """),
            {
                "quiz_id": quiz_id,
                "student_id": student["id"],
                "course_id": student["course_id"],
                "lesson_id": student["lesson_pointer"],
                "question": quiz.question,
                "alternatives": json.dumps([{"id": a.id, "text": a.text} for a in quiz.alternatives]),
                "correct_answer": quiz.correct_answer,
                "explanation": quiz.explanation,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).replace(tzinfo=None),
                "quiz_bank_id": quiz_bank_id,
                "reading_plan_id": reading_plan_id,
                "challenge_id": challenge_id,
            }
        )
        await session.commit()

    sender_id = student["sender_id"]

    # Prepare the body text with full alternatives included
    options_text = "\n".join([f"{alt.id}) {alt.text}" for alt in quiz.alternatives])
    full_body_text = f"{QUIZ_BODY_PREFIX}{quiz.question}\n\n{options_text}"

    # Regra: <=3 alternativas -> Reply Buttons, >3 -> List Message
    if len(quiz.alternatives) <= 3:
        buttons = [
            {"id": f"quiz_{quiz_id}_{alt.id}", "title": f"Alternativa {alt.id}"}
            for alt in quiz.alternatives
        ]
        await channel.send_buttons(
            user_id=sender_id,
            body=full_body_text,
            buttons=buttons,
            footer=QUIZ_FOOTER_BUTTONS,
        )
    else:
        rows = [
            {
                "id": f"quiz_{quiz_id}_{alt.id}",
                "title": f"Alternativa {alt.id}",
                "description": "Toque para selecionar",
            }
            for alt in quiz.alternatives
        ]
        await channel.send_list(
            user_id=sender_id,
            body=full_body_text,
            button_text=QUIZ_LIST_BUTTON_TEXT,
            sections=[{"title": QUIZ_LIST_SECTION_TITLE, "rows": rows}],
            footer=QUIZ_FOOTER_LIST,
        )

    # Vertical de Idiomas: áudio do quiz na LÍNGUA-ALVO (best-effort). O chamador
    # só passa target_language quando o TEXTO do quiz já está na língua-alvo
    # (níveis avançados) — assim o áudio reproduz fielmente o que está escrito.
    if target_language:
        from src.rag.review_generator import synthesize_listening
        try:
            audio = await synthesize_listening(f"{quiz.question}\n{options_text}", target_language)
            if audio:
                await channel.send_audio(sender_id, audio)
        except Exception as e:
            logger.warning("quiz_audio_failed", quiz_id=quiz_id, error=str(e)[:200])

    return quiz_id

async def send_existing_quiz(channel, sender_id: str, quiz_row) -> str:
    """Envia um quiz que já está na tabela PendingQuizzes (fila de Sessão Mista)."""
    quiz_id = quiz_row.quiz_id
    alternatives = json.loads(quiz_row.alternatives)

    # Sessão de Aula: o question_text traz "revisão + pergunta" separados pelo
    # CLASS_REVIEW_SEPARATOR. Enviamos a revisão como TEXTO (que faz chunk) e
    # deixamos só a pergunta no body interativo — cujo limite no WhatsApp é 1024
    # chars (o resumo sozinho passava disso e causava 400). Sem o separador
    # (outros fluxos), o comportamento é o de antes.
    question_text = quiz_row.question_text
    if CLASS_REVIEW_SEPARATOR in question_text:
        review_block, question_text = question_text.split(CLASS_REVIEW_SEPARATOR, 1)
        await channel.send_text(sender_id, review_block)

    options_text = "\n".join([f"{alt['id']}) {alt['text']}" for alt in alternatives])
    full_body_text = f"{QUIZ_BODY_PREFIX}{question_text}\n\n{options_text}"

    if len(alternatives) <= 3:
        buttons = [
            {"id": f"quiz_{quiz_id}_{alt['id']}", "title": f"Alternativa {alt['id']}"}
            for alt in alternatives
        ]
        await channel.send_buttons(
            user_id=sender_id,
            body=full_body_text,
            buttons=buttons,
            footer=QUIZ_FOOTER_BUTTONS,
        )
    else:
        rows = [
            {
                "id": f"quiz_{quiz_id}_{alt['id']}",
                "title": f"Alternativa {alt['id']}",
                "description": "Toque para selecionar",
            }
            for alt in alternatives
        ]
        await channel.send_list(
            user_id=sender_id,
            body=full_body_text,
            button_text=QUIZ_LIST_BUTTON_TEXT,
            sections=[{"title": QUIZ_LIST_SECTION_TITLE, "rows": rows}],
            footer=QUIZ_FOOTER_LIST,
        )

    await channel.send_buttons(
        user_id=sender_id,
        body="Se não quiser responder agora, você pode pular e avançar.",
        buttons=[{"id": f"quiz_skip_{quiz_id}", "title": "Pular Quiz ⏭️"}]
    )

    return quiz_id

async def deliver_next_pending_quiz(channel, sender_id: str, student_id: str, course_id: str, lesson_id: str, exclude_quiz_id: str | None = None) -> bool:
    """Busca o próximo quiz pendente da SESSÃO DE AULA e envia. Retorna True se enviou.

    A fila contínua é exclusiva da Sessão Mista (class_session_worker): só
    entram quizzes marcados pelo CLASS_REVIEW_SEPARATOR e ainda não expirados.
    Sem o filtro, qualquer quiz pendente avulso do mesmo (curso, aula) — revisão
    diária, revisão espaçada, desafio — entraria no meio da sequência da aula
    (e vice-versa: a sequência vazaria para fora do momento da aula).
    """
    from src.db.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        exclude_clause = "AND quiz_id != :exclude_id" if exclude_quiz_id else ""
        params = {
            "student_id": student_id,
            "course_id": course_id,
            "lesson_id": lesson_id,
            "session_marker": CLASS_REVIEW_SEPARATOR,
            "now": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        if exclude_quiz_id:
            params["exclude_id"] = exclude_quiz_id

        result = await session.execute(
            text(f"""
                SELECT * FROM "PendingQuizzes"
                WHERE student_id = :student_id
                  AND course_id = :course_id
                  AND lesson_id = :lesson_id
                  AND status = 'pending'
                  AND position(:session_marker in question_text) > 0
                  AND expires_at > :now
                  {exclude_clause}
                ORDER BY created_at ASC
                LIMIT 1
            """),
            params
        )
        quiz_row = result.fetchone()
        
    if not quiz_row:
        return False
        
    await send_existing_quiz(channel, sender_id, quiz_row)
    return True
