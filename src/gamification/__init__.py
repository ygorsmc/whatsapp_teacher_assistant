"""Gamificação — lógica PURA e determinística (sem I/O, sem LLM).

Esquema de design: `_docs/esquema_gamificacao_whatsapp_teacher_assistant.md`
Operações: `_docs/docs/GAMIFICATION_OPERATIONS.md`

Filosofia: premiar COMPARECER e TENTAR (esforço/consistência), nunca
performance bruta. O cálculo é if/else + timestamps + contadores ($0 por
mensagem). Estes módulos NÃO tocam banco, canal nem rede — são as peças
testáveis isoladamente em `tests/unit/test_gamification.py`. A orquestração de
I/O (escrita do ledger, envio de mensagem, batch semanal) mora em
`src/workers/gamification.py`.

Mestre OFF (`GAMIFICATION_ENABLED=false`) ⇒ nada disto é chamado pelos workers.
"""

from . import xp, anti_gaming, streak, badges, pseudonyms, leaderboard, export, grade

__all__ = [
    "xp", "anti_gaming", "streak", "badges", "pseudonyms",
    "leaderboard", "export", "grade",
]
