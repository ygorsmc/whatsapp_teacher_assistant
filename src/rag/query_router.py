"""
Query Router (F6) — roteamento determinístico de referências exatas.

Regex puros (testáveis, custo zero) que detectam na pergunta do aluno:
  - referências de exercício/questão  ("exercício 3.2", "questão 5")
  - referências de aula/capítulo/seção ("aula 4", "capítulo 3", "seção 2.1")
  - siglas (2-6 maiúsculas: PIB, ABNT, HTTP)
  - números de norma/lei ("NBR 6118", "Lei 8.666/93", "ISO 9001")

Referência detectada → pinpoint OBRIGATÓRIO de lesson_number/material e
reforço do termo literal no braço esparso da busca híbrida (o calcanhar do
ensino são os termos exatos que o embedding denso dilui).
"""

import re
from dataclasses import dataclass, field
from typing import Optional

# Aula/lição/módulo — aponta para lesson_number de material 'lesson'.
LESSON_REF_REGEX = re.compile(r"\b(?:aula|li[çc][ãa]o|m[óo]dulo)\s+(\d{1,3})\b", re.IGNORECASE)

# Capítulo/seção — em turmas com livro aponta para o capítulo (lesson_number
# do material denso); em turmas só de aulas é tratado como aula (legado).
CHAPTER_REF_REGEX = re.compile(r"\b(?:cap[íi]tulo|cap\.?|se[çc][ãa]o)\s+(\d{1,3})\b", re.IGNORECASE)

# Exercício N.M (capítulo.exercício) e exercício/questão N.
EXERCISE_DOTTED_REGEX = re.compile(r"\bexerc[íi]cio\s+(\d{1,3})\.(\d{1,3})\b", re.IGNORECASE)
EXERCISE_PLAIN_REGEX = re.compile(r"\b(?:exerc[íi]cio|quest[ãa]o)\s+(\d{1,3})\b", re.IGNORECASE)

# Siglas: 2-6 maiúsculas (com acentos), bordas de palavra. Stoplist evita
# falsos positivos de palavras comuns gritadas/início de frase.
ACRONYM_REGEX = re.compile(r"\b([A-ZÀ-Ü]{2,6})\b")
_ACRONYM_STOPLIST = {
    "OK", "EU", "ME", "TE", "SE", "DE", "DA", "DO", "EM", "NA", "NO", "UM",
    "AS", "OS", "AO", "POR", "QUE", "COM", "NAO", "NÃO", "SIM", "MAS", "OI",
    "OLA", "OLÁ", "PRA", "PRO", "ELE", "ELA", "ISSO", "ESSA", "ESSE",
}

# Normas e leis: "NBR 6118", "ABNT NBR 14724", "ISO 9001", "Lei 8.666/93",
# "Lei nº 8.112", "Decreto 10.024", "Portaria 343", "Resolução 466/12".
NORM_REGEX = re.compile(
    r"\b((?:ABNT\s+)?(?:NBR|ISO|IEC|DIN|ASTM)\s*\d{2,6}(?:[-:]\d+)?"
    r"|(?:lei|decreto|portaria|resolu[çc][ãa]o|s[úu]mula)\s+(?:n[ºo°.]?\s*)?\d{1,6}(?:\.\d{3})*(?:/\d{2,4})?)\b",
    re.IGNORECASE,
)


@dataclass
class RouteResult:
    """Referências exatas detectadas — guiam o pinpoint e o braço esparso."""
    lesson_ref: Optional[int] = None          # "aula 4" → 4
    chapter_ref: Optional[int] = None         # "capítulo 3" / "exercício 3.2" → 3
    exercise_ref: Optional[str] = None        # canônico "cap3_ex2" (AnswerKeys)
    question_number: Optional[int] = None     # "questão 5" → 5
    exact_terms: list[str] = field(default_factory=list)  # reforço lexical

    @property
    def has_exact_reference(self) -> bool:
        return bool(
            self.lesson_ref or self.chapter_ref or self.exercise_ref
            or self.question_number or self.exact_terms
        )


def route_query(query: str) -> RouteResult:
    """Analisa a pergunta e devolve as referências exatas detectadas."""
    result = RouteResult()
    if not query:
        return result

    lesson = LESSON_REF_REGEX.search(query)
    if lesson:
        result.lesson_ref = int(lesson.group(1))

    chapter = CHAPTER_REF_REGEX.search(query)
    if chapter:
        result.chapter_ref = int(chapter.group(1))

    dotted = EXERCISE_DOTTED_REGEX.search(query)
    if dotted:
        result.chapter_ref = int(dotted.group(1))
        result.exercise_ref = f"cap{dotted.group(1)}_ex{dotted.group(2)}"
        result.exact_terms.append(dotted.group(0))
    else:
        plain = EXERCISE_PLAIN_REGEX.search(query)
        if plain:
            result.question_number = int(plain.group(1))
            result.exact_terms.append(plain.group(0))
            if result.chapter_ref:
                # "exercício 2 do capítulo 3" → referência canônica de gabarito
                result.exercise_ref = f"cap{result.chapter_ref}_ex{plain.group(1)}"

    for match in NORM_REGEX.finditer(query):
        result.exact_terms.append(match.group(1))

    # Siglas: ignora as que já fazem parte de uma norma capturada acima.
    norm_text = " ".join(result.exact_terms).upper()
    for match in ACRONYM_REGEX.finditer(query):
        token = match.group(1)
        if token.upper() in _ACRONYM_STOPLIST:
            continue
        if token.upper() in norm_text:
            continue
        result.exact_terms.append(token)

    return result


# ── F4 (Vertical de Idiomas) — intenção de ROLEPLAY (regex puro, testável) ──────
_ROLEPLAY_INTENT_REGEX = re.compile(
    r"\b(role\s?play|encen[ae]|vamos\s+(?:praticar|treinar|conversar|simular)"
    r"|simular?\s+(?:uma\s+)?(?:conversa|di[áa]logo|situa[çc][ãa]o)"
    r"|pratica(?:r)?\s+(?:comigo|conversa|conversa[çc][ãa]o|di[áa]logo))\b",
    re.IGNORECASE,
)
_ROLEPLAY_SCENARIO_REGEX = re.compile(
    r"\b(?:n[oa]|numa?|em\s+uma?|sobre|de)\s+([0-9A-Za-zÀ-ü][\wÀ-ü ]{2,38})", re.IGNORECASE
)
# "sair" é reservado ao opt-out de mensagens (convenção da Meta) — a saída do
# roleplay usa parar/encerrar/chega/terminar/stop para não colidir.
_ROLEPLAY_EXIT_REGEX = re.compile(
    r"^\s*(parar|encerrar|chega|terminar|stop)\b", re.IGNORECASE
)


def detect_roleplay_intent(query: str) -> Optional[str]:
    """Intenção de roleplay? Retorna o CENÁRIO (best-effort) ou "" (genérico) se
    detectado; None caso contrário. Puro/testável."""
    if not query or not _ROLEPLAY_INTENT_REGEX.search(query):
        return None
    scenario = _ROLEPLAY_SCENARIO_REGEX.search(query)
    return scenario.group(1).strip() if scenario else ""


def is_roleplay_exit(query: str) -> bool:
    """O aluno quer encerrar o roleplay? (só consultado com roleplay ativo). Puro."""
    return bool(query and _ROLEPLAY_EXIT_REGEX.search(query))


def sparse_query_text(query: str, route: RouteResult) -> str:
    """Texto do braço esparso: a query + reforço dos termos literais detectados.

    Repetir o termo exato aumenta seu peso lexical na fusão — barato e
    determinístico (não altera o braço denso).
    """
    if not route.exact_terms:
        return query
    return query + " " + " ".join(route.exact_terms)
