"""
Escopo dos fluxos de quiz — regressão da "cascata de sessão".

Bug corrigido: responder QUALQUER quiz (ex.: revisão diária/espaçada) drenava a
fila packed da Sessão Mista do mesmo (curso, aula) — o aluno respondia uma
revisão às 19h e recebia "Revisão do Tópico (1/8)" na sequência, fora do
momento da aula. A fila da sessão agora é um circuito fechado (marcador
CLASS_REVIEW_SEPARATOR) e as janelas agendadas da aba Alunos priorizam a
revisão espaçada vencida (D+3/D+10/D+30) antes de enviar quiz inédito.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.shared.constants.messages import CLASS_REVIEW_SEPARATOR, ERROR_NO_MATERIAL


def _mock_session(mocker, target: str, execute_side_effect):
    """Sessão async mockada no padrão da casa (test_edge_resilience)."""
    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    mocker.patch(target, return_value=cm)
    return session


def _quiz_row(question_text: str):
    return SimpleNamespace(
        quiz_id="QID", correct_answer="A", explanation="pq sim",
        question_text=question_text, status="pending", quiz_bank_id=None,
        reading_plan_id=None, lesson_id="8", course_id="crs_1", created_at=None,
    )


def _patch_quiz_answer_hooks(mocker, worker):
    mocker.patch.object(worker, "track_event")
    mocker.patch.object(worker, "_find_linked_chapter", new=AsyncMock(return_value=None))
    mocker.patch("src.workers.gamification.award_quiz_xp", new=AsyncMock(return_value=None))
    mocker.patch("src.workers.gamification.award_xp", new=AsyncMock(return_value=None))


# ───────── quiz avulso (revisão diária/espaçada) NÃO drena a fila da sessão ─────
@pytest.mark.asyncio
async def test_quiz_avulso_nao_encadeia_fila_da_sessao(mocker):
    """Responder um quiz SEM o marcador da Sessão Mista não pode puxar o
    próximo pendente — era a cascata "(1/8)" logo após a revisão espaçada."""
    import src.workers.llm_worker as worker

    select_result = Mock()
    select_result.fetchone.return_value = _quiz_row("Pergunta avulsa da revisão diária?")
    update_result = Mock()
    update_result.rowcount = 1
    _mock_session(mocker, "src.db.database.AsyncSessionLocal", [select_result, update_result])
    _patch_quiz_answer_hooks(mocker, worker)

    deliver = mocker.patch(
        "src.workers.micro_learning.deliver_next_pending_quiz",
        new=AsyncMock(return_value=True),
    )

    channel = MagicMock()
    channel.send_text = AsyncMock()
    await worker.handle_quiz_response(channel, "5511999990000", "stu_1",
                                      "quiz_QID_A", {}, message_id="m1")

    deliver.assert_not_called()
    # Só o feedback foi enviado — nada de "Revisão do Tópico (1/8)" na sequência.
    assert channel.send_text.await_count == 1


# ───────── quiz DA sessão continua encadeando (e fecha com a mensagem final) ────
@pytest.mark.asyncio
async def test_quiz_da_sessao_encadeia_proximo_topico(mocker):
    import src.workers.llm_worker as worker

    question = f"📖 *Revisão do Tópico (2/8):*\nresumo{CLASS_REVIEW_SEPARATOR}Pergunta da sessão?"
    select_result = Mock()
    select_result.fetchone.return_value = _quiz_row(question)
    update_result = Mock()
    update_result.rowcount = 1
    _mock_session(mocker, "src.db.database.AsyncSessionLocal", [select_result, update_result])
    _patch_quiz_answer_hooks(mocker, worker)

    deliver = mocker.patch(
        "src.workers.micro_learning.deliver_next_pending_quiz",
        new=AsyncMock(return_value=False),  # fila vazia → mensagem de conclusão
    )

    channel = MagicMock()
    channel.send_text = AsyncMock()
    await worker.handle_quiz_response(channel, "5511999990000", "stu_1",
                                      "quiz_QID_A", {}, message_id="m1")

    deliver.assert_awaited_once()
    textos = [c.args[1] for c in channel.send_text.await_args_list]
    assert any("concluiu todos os tópicos" in t for t in textos)


# ───────── a fila da sessão filtra pelo marcador e ignora quizzes expirados ─────
@pytest.mark.asyncio
async def test_fila_da_sessao_filtra_marcador_e_expiracao(mocker):
    from src.workers.micro_learning import deliver_next_pending_quiz

    empty = Mock()
    empty.fetchone.return_value = None
    session = _mock_session(mocker, "src.db.database.AsyncSessionLocal", [empty])

    channel = MagicMock()
    sent = await deliver_next_pending_quiz(channel, "5511999990000", "stu_1", "crs_1", "8")
    assert sent is False

    sql = str(session.execute.await_args.args[0])
    params = session.execute.await_args.args[1]
    assert "position(:session_marker in question_text) > 0" in sql
    assert "expires_at > :now" in sql
    assert params["session_marker"] == CLASS_REVIEW_SEPARATOR


# ───────── janela agendada: revisão espaçada vencida tem prioridade ─────────────
def _student_context():
    return {
        "teacher": SimpleNamespace(id="tea_1", name="Prof", subject="IA"),
        "student": SimpleNamespace(id="stu_1", name="Ygor"),
        "course": SimpleNamespace(id="crs_1", name="IA nos Negócios"),
        "subject": None,
        "class_obj": SimpleNamespace(
            current_lesson_number=8, is_language=False, proficiency_level=None
        ),
    }


@pytest.mark.asyncio
async def test_janela_prioriza_revisao_espacada_vencida(mocker):
    """Com SRS vencida, a janela entrega a reentrega ($0) e NÃO gera quiz novo."""
    import src.workers.llm_worker as worker

    srs = mocker.patch(
        "src.workers.spaced_repetition.deliver_due_srs_for_student",
        new=AsyncMock(return_value=True),
    )
    gen = mocker.patch.object(worker, "generate_review", new=AsyncMock())

    channel = MagicMock()
    channel.send_text = AsyncMock()
    await worker.handle_review_request(channel, "5511999990000", _student_context())

    srs.assert_awaited_once_with(channel, "5511999990000", "stu_1", "crs_1")
    gen.assert_not_called()
    channel.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_janela_sem_srs_vencida_segue_fluxo_normal(mocker):
    """Sem SRS vencida (antes do D+3), a janela envia quiz ainda não enviado
    (fluxo clássico via generate_review/QuizBank)."""
    import src.workers.llm_worker as worker

    mocker.patch(
        "src.workers.spaced_repetition.deliver_due_srs_for_student",
        new=AsyncMock(return_value=False),
    )
    mocker.patch("src.rag.prompt_builder.resolve_lesson_label",
                 new=AsyncMock(return_value="Aula 8"))
    mocker.patch("src.rag.review_generator.class_language_context",
                 return_value=(None, None))
    gen = mocker.patch.object(worker, "generate_review", new=AsyncMock(return_value=None))

    channel = MagicMock()
    channel.send_text = AsyncMock()
    await worker.handle_review_request(channel, "5511999990000", _student_context())

    gen.assert_awaited_once()
    channel.send_text.assert_awaited_once_with("5511999990000", ERROR_NO_MATERIAL)


# ───────── entrega na janela: 1 por janela; sem vencida → False ────────────────
@pytest.mark.asyncio
async def test_deliver_due_srs_sem_vencidas_retorna_false(mocker):
    import src.workers.spaced_repetition as sr

    empty = Mock()
    empty.fetchall.return_value = []
    _mock_session(mocker, "src.workers.spaced_repetition.AsyncSessionLocal", [empty])
    send = mocker.patch.object(sr, "_send_srs_item", new=AsyncMock())

    ok = await sr.deliver_due_srs_for_student(MagicMock(), "5511999990000", "stu_1", "crs_1")
    assert ok is False
    send.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_due_srs_envia_uma_por_janela(mocker):
    import src.workers.spaced_repetition as sr

    item1, item2 = SimpleNamespace(srq_id="srq1"), SimpleNamespace(srq_id="srq2")
    rows = Mock()
    rows.fetchall.return_value = [item1, item2]
    _mock_session(mocker, "src.workers.spaced_repetition.AsyncSessionLocal", [rows])
    send = mocker.patch.object(sr, "_send_srs_item", new=AsyncMock(return_value=True))

    ok = await sr.deliver_due_srs_for_student(MagicMock(), "5511999990000", "stu_1", "crs_1")
    assert ok is True
    send.assert_awaited_once()  # microdose: uma revisão por janela


# ───────── free-run do scheduler não duplica quem tem janela agendada ──────────
@pytest.mark.asyncio
async def test_free_run_exclui_alunos_com_janela_agendada(mocker):
    """Aluno com StudentSchedule ativo no curso recebe a SRS NA janela
    (prioridade) — o free-run de 5 min só cobre quem não tem agendamento
    (ex.: leitores via QR)."""
    import src.workers.spaced_repetition as sr

    empty = Mock()
    empty.fetchall.return_value = []
    session = _mock_session(mocker, "src.workers.spaced_repetition.AsyncSessionLocal", [empty])

    await sr.process_spaced_repetition()

    sql = str(session.execute.await_args.args[0])
    assert "NOT EXISTS" in sql
    assert '"StudentSchedule"' in sql
