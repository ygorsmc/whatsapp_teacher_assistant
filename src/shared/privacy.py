"""
Privacidade / LGPD — helpers puros (sem I/O), cobertos por testes.

Implementa o Plano LGPD (`_docs/docs/PLANO_LGPD.md`):
  - P0.1 Minimização de PII para provedores de IA: só o PRIMEIRO NOME do
    aluno entra em prompts (o LLM não precisa do sobrenome para ser
    pedagógico).
  - P1.4 Higiene de telemetria: telefone/chat_id mascarado nos logs
    estruturados (App Insights é um terceiro — PII não viaja em claro).
  - P1.1 Cláusula SQL do gate de consentimento parental (Art. 14): filtra
    crianças sem opt-in do responsável das entregas proativas.
"""

from src.shared.config import settings


def first_name(full_name: str | None) -> str:
    """Primeiro nome do titular — o que vai a prompts de LLM (P0.1).

    Vazio/None → rótulo neutro (o prompt continua funcional sem PII).
    """
    if not full_name or not full_name.strip():
        return "aluno(a)"
    return full_name.strip().split()[0]


def llm_safe_name(full_name: str | None) -> str:
    """Nome do aluno como deve sair para QUALQUER provedor de IA.

    Com `LLM_PII_MINIMIZATION` (default ON): primeiro nome. Desligada
    (depuração/contrato específico): nome completo, comportamento legado.
    """
    if settings.LLM_PII_MINIMIZATION:
        return first_name(full_name)
    return (full_name or "").strip() or "aluno(a)"


def mask_contact(contact: str | None) -> str:
    """Mascara telefone/chat_id para logs: '5541999887766' → '…7766'.

    Mantém os 4 últimos dígitos — suficiente para correlacionar um caso em
    investigação sem expor o contato inteiro na telemetria.
    """
    if not contact:
        return "?"
    contact = str(contact)
    if len(contact) <= 4:
        return "…" + contact
    return "…" + contact[-4:]


def guardian_consent_sql_clause(student_alias: str = "s") -> str:
    """Cláusula AND para queries de ENTREGA PROATIVA (Art. 14, P1.1).

    Com `GUARDIAN_CONSENT_ENABLED`: exclui alunos marcados como criança
    (`is_child`) sem consentimento 'granted' do responsável. Flag off → ""
    (comportamento atual intocado — regra da casa).
    """
    if not settings.GUARDIAN_CONSENT_ENABLED:
        return ""
    return (
        f' AND ({student_alias}.is_child = false OR EXISTS '
        f'(SELECT 1 FROM "GuardianConsents" gc '
        f'WHERE gc.student_id = {student_alias}.id AND gc.status = \'granted\'))'
    )
