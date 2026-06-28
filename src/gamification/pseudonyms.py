"""Pseudônimos anônimos da liga — animal + cor, REATRIBUÍDOS por semana.

O sistema gera o pseudônimo (nunca o nome real do menor). Reatribuir a cada
semana impede o rastreio longitudinal por colegas ("descobrir quem é o 🐢 da
semana passada") — é o que torna a exposição de menor defensável sob a LGPD
(Art. 14) e nos coloca à frente do Duolingo (que usa username fixo).

Pura e determinística: dado (student_id, week_id) e a coorte, a atribuição é
estável dentro da semana e SEM colisão dentro da coorte.
"""

from __future__ import annotations

import hashlib

# Pool fixo de animais (emoji + nome). ~24 entradas × adjetivos = espaço amplo.
ANIMAL_POOL: tuple[tuple[str, str], ...] = (
    ("🦉", "Coruja"), ("🦊", "Raposa"), ("🐢", "Tartaruga"), ("🦅", "Águia"),
    ("🐬", "Golfinho"), ("🦫", "Castor"), ("🦌", "Cervo"), ("🦜", "Arara"),
    ("🐧", "Pinguim"), ("🦦", "Lontra"), ("🐝", "Abelha"), ("🦂", "Escorpião"),
    ("🐙", "Polvo"), ("🦚", "Pavão"), ("🦩", "Flamingo"), ("🐊", "Jacaré"),
    ("🦏", "Rinoceronte"), ("🐉", "Dragão"), ("🦘", "Canguru"), ("🦥", "Preguiça"),
    ("🐿️", "Esquilo"), ("🦔", "Ouriço"), ("🦤", "Dodô"), ("🦢", "Cisne"),
)

# Adjetivos/cores (concordância no feminino — todos os animais do pool acima
# têm rótulo feminino exceto alguns; mantemos adjetivos neutros/femininos comuns).
ADJECTIVES: tuple[str, ...] = (
    "Atenta", "Veloz", "Sábia", "Azul", "Curiosa", "Tranquila",
    "Dourada", "Esperta", "Serena", "Brava", "Ágil", "Radiante",
)


def _hash_int(*parts: str) -> int:
    raw = "::".join(parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def pseudonym_combo(student_id: str, week_id: str) -> tuple[int, int]:
    """Índices (animal, adjetivo) determinísticos para (aluno, semana).

    Sem garantia de unicidade dentro da coorte — use `assign_unique` para isso.
    """
    h = _hash_int(student_id, week_id)
    return (h % len(ANIMAL_POOL), (h // len(ANIMAL_POOL)) % len(ADJECTIVES))


def format_pseudonym(animal_idx: int, adjective_idx: int) -> str:
    emoji, animal = ANIMAL_POOL[animal_idx % len(ANIMAL_POOL)]
    adjective = ADJECTIVES[adjective_idx % len(ADJECTIVES)]
    return f"{emoji} {animal} {adjective}"


def pseudonym_for(student_id: str, week_id: str) -> str:
    """Pseudônimo simples (pode colidir em coortes grandes)."""
    a, b = pseudonym_combo(student_id, week_id)
    return format_pseudonym(a, b)


def assign_unique(student_ids: list[str], week_id: str) -> dict[str, str]:
    """Atribui pseudônimos SEM colisão dentro da coorte, de forma determinística.

    Ordena os alunos por um hash estável (evita depender da ordem de entrada),
    e em caso de colisão sonda a próxima combinação livre (também determinística).
    Espaço = |animais| × |adjetivos|; se a coorte exceder isso (não deveria, teto
    ~30), cai num sufixo numérico para garantir unicidade.
    """
    ordered = sorted(student_ids, key=lambda sid: _hash_int(sid, week_id))
    used: set[str] = set()
    result: dict[str, str] = {}
    total_combos = len(ANIMAL_POOL) * len(ADJECTIVES)

    for sid in ordered:
        a, b = pseudonym_combo(sid, week_id)
        start = a * len(ADJECTIVES) + b
        chosen: str | None = None
        for step in range(total_combos):
            idx = (start + step) % total_combos
            cand = format_pseudonym(idx // len(ADJECTIVES), idx % len(ADJECTIVES))
            if cand not in used:
                chosen = cand
                break
        if chosen is None:  # coorte maior que o pool inteiro (defensivo)
            chosen = f"{pseudonym_for(sid, week_id)} #{len(result) + 1}"
        used.add(chosen)
        result[sid] = chosen
    return result
