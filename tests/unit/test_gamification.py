"""Testes da lógica PURA de gamificação (Fase 0).

Cobre as peças determinísticas de `src/gamification/` — XP, anti-gaming, streak
(escudo/marco/quebra), badges, pseudônimos e leaderboard. Sem I/O, sem banco:
a mesma régua que protege challenges.py/spaced_repetition.py.
"""

from datetime import datetime, date, timedelta

import pytest

from src.gamification import xp, anti_gaming, badges, pseudonyms, leaderboard, export
from src.gamification import streak as streak_mod


# ─────────────────────────── XP ───────────────────────────

def test_week_id_is_iso_week_brt():
    assert xp.week_id_for(datetime(2026, 6, 27)) == "2026-W26"
    # Segunda 00:00 vira a fronteira: dias da mesma semana ISO casam.
    assert xp.week_id_for(datetime(2026, 6, 22)) == xp.week_id_for(datetime(2026, 6, 27))


def test_base_xp_known_and_unknown():
    w = {"quiz_correct": 10, "quiz_wrong": 4, "srs_ontime": 8}
    assert xp.base_xp_for("quiz_correct", weights=w) == 10
    assert xp.base_xp_for("quiz_wrong", weights=w) == 4
    assert xp.base_xp_for("inexistente", weights=w) == 0


def test_daily_cap_clamps():
    assert xp.cap_remaining(65, 70) == 5
    assert xp.cap_remaining(80, 70) == 0          # nunca negativo
    assert xp.clamp_to_cap(20, 65, 70) == 5       # só cabe o que resta
    assert xp.clamp_to_cap(20, 0, 70) == 20


def test_consistency_bonus_only_first_act():
    assert xp.consistency_bonus(True, bonus=5) == 5
    assert xp.consistency_bonus(False, bonus=5) == 0


# ─────────────────────── Anti-gaming ───────────────────────

def test_flash_answer_detection():
    sent = datetime(2026, 6, 27, 12, 0, 0)
    assert anti_gaming.is_flash_answer(sent, sent + timedelta(seconds=1), threshold_seconds=3) is True
    assert anti_gaming.is_flash_answer(sent, sent + timedelta(seconds=10), threshold_seconds=3) is False
    # timestamps ausentes ou invertidos nunca acusam (best-effort).
    assert anti_gaming.is_flash_answer(None, sent, threshold_seconds=3) is False
    assert anti_gaming.is_flash_answer(sent, sent - timedelta(seconds=5), threshold_seconds=3) is False


def test_repetitive_pattern_needs_both_signals():
    # Mesma alternativa repetida E baixa acurácia → suspeito.
    assert anti_gaming.is_repetitive_pattern(["C", "C", "C", "C"], [False, False, True, False]) is True
    # Mesma alternativa MAS acertando muito → NÃO acusa.
    assert anti_gaming.is_repetitive_pattern(["C", "C", "C", "C"], [True, True, True, True]) is False
    # Alternativas variadas → não é padrão.
    assert anti_gaming.is_repetitive_pattern(["A", "B", "C", "D"], [False, False, False, False]) is False
    # Sequência curta → não dá pra concluir.
    assert anti_gaming.is_repetitive_pattern(["C", "C"], [False, False]) is False


def test_rag_doubt_cap():
    assert anti_gaming.rag_doubt_over_cap(2, cap=3) is False
    assert anti_gaming.rag_doubt_over_cap(3, cap=3) is True


# ──────────────────────────── Streak ───────────────────────────

def test_streak_first_activity():
    out = streak_mod.apply_activity(streak_mod.StreakState(), date(2026, 6, 27))
    assert out.state.streak == 1
    assert out.state.best == 1
    assert out.incremented is True


def test_streak_consecutive_and_milestone():
    s = streak_mod.StreakState(streak=2, best=2, shields=0, last_active_date=date(2026, 6, 26))
    out = streak_mod.apply_activity(s, date(2026, 6, 27))
    assert out.state.streak == 3
    assert out.milestone == 3                      # marco 3 batido hoje


def test_streak_shield_earned_on_multiple_of_seven():
    s = streak_mod.StreakState(streak=6, best=6, shields=0, last_active_date=date(2026, 6, 26))
    out = streak_mod.apply_activity(s, date(2026, 6, 27))
    assert out.state.streak == 7
    assert out.shield_earned == 1
    assert out.state.shields == 1


def test_streak_shield_consumed_on_one_day_miss():
    # Faltou 1 dia (gap de 2) mas tinha escudo: emenda e preserva.
    s = streak_mod.StreakState(streak=10, best=12, shields=1, last_active_date=date(2026, 6, 25))
    out = streak_mod.apply_activity(s, date(2026, 6, 27))
    assert out.state.streak == 11
    assert out.shield_consumed == 1
    assert out.state.shields == 0
    assert out.reset is False


def test_streak_breaks_without_shield_but_best_persists():
    s = streak_mod.StreakState(streak=10, best=12, shields=0, last_active_date=date(2026, 6, 25))
    out = streak_mod.apply_activity(s, date(2026, 6, 27))
    assert out.reset is True
    assert out.state.streak == 1                    # recomeça hoje
    assert out.state.best == 12                     # recorde de pé


def test_streak_idempotent_same_day():
    s = streak_mod.StreakState(streak=5, best=5, shields=0, last_active_date=date(2026, 6, 27))
    out = streak_mod.apply_activity(s, date(2026, 6, 27))
    assert out.already_active_today is True
    assert out.state.streak == 5                    # não conta 2x no mesmo dia


def test_streak_multi_day_miss_insufficient_shields_resets():
    # Faltaram 3 dias, só 1 escudo: não cobre → reseta.
    s = streak_mod.StreakState(streak=20, best=20, shields=1, last_active_date=date(2026, 6, 23))
    out = streak_mod.apply_activity(s, date(2026, 6, 27))
    assert out.reset is True
    assert out.state.streak == 1
    assert out.state.shields == 1                   # escudo não foi gasto à toa


def test_shield_cap_at_two():
    s = streak_mod.StreakState(streak=13, best=13, shields=2, last_active_date=date(2026, 6, 26))
    out = streak_mod.apply_activity(s, date(2026, 6, 27))  # vira 14 (múltiplo de 7)
    assert out.state.streak == 14
    assert out.state.shields == 2                   # não passa do teto


# ──────────────────────────── Badges ───────────────────────────

def test_badge_level_thresholds():
    chama = badges.BADGE_BY_CODE["chama_viva"]
    assert badges.level_for(chama, 2) is None
    assert badges.level_for(chama, 3) == "bronze"
    assert badges.level_for(chama, 7) == "prata"
    assert badges.level_for(chama, 30) == "ouro"
    assert badges.level_for(chama, 100) == "lendaria"


def test_badge_evaluate_and_next():
    unlocked = dict(badges.evaluate({"streak_best": 30, "rag_doubts": 11, "srs_ontime": 5}))
    assert unlocked["chama_viva"] == "ouro"
    assert unlocked["mente_curiosa"] == "bronze"
    assert "pontual" not in unlocked                # 5 < 10 (bronze de Pontual)
    nxt = badges.next_threshold(badges.BADGE_BY_CODE["mente_curiosa"], 11)
    assert nxt == ("prata", 30)


def test_level_rank_order():
    assert badges.level_rank(None) < badges.level_rank("bronze") < badges.level_rank("ouro")


# ──────────────────────── Pseudônimos ───────────────────────

def test_pseudonyms_unique_within_cohort():
    ids = [f"s{i}" for i in range(30)]
    m = pseudonyms.assign_unique(ids, "2026-W26")
    assert len(set(m.values())) == len(ids)         # sem colisão


def test_pseudonyms_stable_within_week_change_across_weeks():
    ids = ["a", "b", "c"]
    w26 = pseudonyms.assign_unique(ids, "2026-W26")
    w26_again = pseudonyms.assign_unique(ids, "2026-W26")
    w27 = pseudonyms.assign_unique(ids, "2026-W27")
    assert w26 == w26_again                          # determinístico na semana
    # A reatribuição semanal muda ao menos um pseudônimo (anti-rastreio).
    assert any(w26[i] != w27[i] for i in ids)


# ──────────────────────── Leaderboard ───────────────────────

def test_partition_single_cohort_when_small():
    items = [("a", 10), ("b", 8), ("c", 5)]
    cohorts = leaderboard.partition_into_cohorts(items, target_size=30)
    assert len(cohorts) == 1
    assert cohorts[0] == items


def test_partition_balanced_chunks_when_large():
    items = [(f"s{i}", 100 - i) for i in range(50)]  # já ordenado desc
    cohorts = leaderboard.partition_into_cohorts(items, target_size=30)
    assert len(cohorts) == 2
    # Equilíbrio: blocos de 25 cada (nada de bloco órfão minúsculo).
    assert sorted(len(c) for c in cohorts) == [25, 25]
    # Pareado por engajamento: o topo fica todo na 1ª coorte.
    assert cohorts[0][0][0] == "s0"


def test_rank_cohort_orders_desc():
    ranked = leaderboard.rank_cohort([("a", 5), ("b", 9), ("c", 9)])
    assert [(e.student_id, e.rank) for e in ranked] == [("b", 1), ("c", 2), ("a", 3)]


def test_league_view_top3_neighbors_and_gap():
    ranked = leaderboard.rank_cohort(
        [("a", 120), ("b", 102), ("c", 95), ("d", 88), ("me", 85), ("e", 80)]
    )
    v = leaderboard.build_league_view(ranked, "me", min_cohort=3)
    assert v.circulates is True
    assert [e.student_id for e in v.top3] == ["a", "b", "c"]
    assert [e.student_id for e in v.neighbors] == ["d", "me", "e"]
    assert v.xp_to_next == 3                          # 88 - 85
    assert v.my_rank == 5


def test_league_view_small_cohort_does_not_circulate():
    ranked = leaderboard.rank_cohort([("me", 10), ("x", 8)])
    v = leaderboard.build_league_view(ranked, "me", min_cohort=3)
    assert v.circulates is False                      # < min → só progresso pessoal


def test_league_view_leader_has_no_next():
    ranked = leaderboard.rank_cohort([("me", 50), ("x", 40), ("y", 30)])
    v = leaderboard.build_league_view(ranked, "me", min_cohort=3)
    assert v.my_rank == 1
    assert v.xp_to_next is None


# ──────────────── Ligas (Fase 1) — promo/rebaixamento ───────────────

def test_division_promote_relegate_clamps_at_ends():
    assert leaderboard.promote("bronze") == "prata"
    assert leaderboard.promote("diamante") == "diamante"     # topo não sobe mais
    assert leaderboard.relegate("ouro") == "prata"
    assert leaderboard.relegate("bronze") == "bronze"        # fundo não cai mais
    assert leaderboard.division_index(None) == 0             # None ⇒ mais baixa


def test_league_movement_top_bottom_middle():
    # Coorte grande: topo sobe, fundo desce, meio fica.
    assert leaderboard.apply_league_movement("ouro", 1, 30) == ("safira", "promoted")
    assert leaderboard.apply_league_movement("ouro", 28, 30) == ("prata", "relegated")
    assert leaderboard.apply_league_movement("ouro", 15, 30) == ("ouro", "stay")


def test_league_movement_small_cohort_everyone_stays():
    assert leaderboard.apply_league_movement("ouro", 1, 4) == ("ouro", "stay")


def test_league_movement_edges_stay_when_already_extreme():
    # Topo da divisão MAIS ALTA não promove; fundo da MAIS BAIXA não rebaixa.
    assert leaderboard.apply_league_movement("diamante", 1, 30) == ("diamante", "stay")
    assert leaderboard.apply_league_movement("bronze", 30, 30) == ("bronze", "stay")


# ──────────────── Badges secretos (Fase 1) — catálogo ───────────────

def test_secret_badges_catalog():
    codes = {b.code for b in badges.SECRET_BADGES}
    assert codes == {"coruja_filosofica", "fenix", "cacador_erratas"}
    assert badges.SECRET_LEVEL == "secreta"
    # cada secreto tem dica de descoberta (revelação).
    assert all(b.hint for b in badges.SECRET_BADGES)


# ──────────────── Exportação (Fase 1, Modelo A) ─────────────────────

def test_export_rows_shape_and_header():
    recs = [export.EngagementRecord("Ana", 5, 12, 8, 3, 7, 85)]
    rows = export.build_engagement_export_rows(recs)
    assert rows[0] == export.EXPORT_HEADER
    assert rows[1] == ["Ana", "5", "12", "8", "3", "7", "85"]


def test_export_csv_escapes_commas_and_quotes():
    recs = [export.EngagementRecord('Ana, "B"', 1, 0, 0, 0, 0, 0)]
    csv = export.rows_to_csv(export.build_engagement_export_rows(recs))
    # Nome com vírgula/aspas é envolto em aspas e as aspas internas são dobradas.
    assert '"Ana, ""B"""' in csv


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
