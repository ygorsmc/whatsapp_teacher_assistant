"""
Review Generator — Generates pedagogical summaries with mandatory quiz
from content indexed via Azure Document Intelligence in Qdrant.

REFATORADO: Banco de Questões Emergente (Cache Semântico "Lazy").

Fluxo por requisição de quiz/revisão:
  1. SORTEIO DO TÓPICO (Redis) — checa a lista de tópicos da aula no Redis.
     Se não existir (primeiro aluno da turma a pedir), o LLM analisa o RAG da
     aula, cria a lista e a salva no Redis. O tópico é "puxado" respeitando a
     rotação individual do aluno (used_topics).
  2. CACHE HIT (QuizBank) — busca no banco uma questão desse tópico que o
     aluno ainda não recebeu. Achou → envia direto do banco (custo $0,
     bypass total do LLM e do RAG).
  3. CACHE MISS — sorteia uma Lente Pedagógica INÉDITA para o tópico (uma que
     ainda não exista no banco), invoca o LLM (Gemini/provider configurado),
     SALVA o novo pacote no QuizBank e envia. A turma toda passa a aproveitar.

O pacote cacheado inclui resumo + pontos-chave + quiz, de modo que o Cache Hit
reproduz a experiência completa sem nenhuma chamada de IA.
"""

import json
import random
from typing import Optional, cast

import structlog
from pydantic import BaseModel, Field

from src.llm.factory import get_provider
from src.rag.retriever import retrieve_context
from src.rag.quiz_generator import QuizAlternative
from src.rag import quiz_bank
from src.shared.config import settings
from src.shared.language import (
    LanguageDelivery,
    detect_language_course,
    language_delivery,
    language_name,
)
from src.shared.constants.prompts import (
    REVIEW_PROMPT_TEMPLATE,
    READING_REVIEW_PROMPT_TEMPLATE,
    LISTENING_PROMPT_TEMPLATE,
    LANGUAGE_REVIEW_PROMPT_TEMPLATE,
    VOCAB_GENERATION_PROMPT,
    EXAM_PREP_PROMPT,
    TOPIC_EXTRACTION_PROMPT,
)

logger = structlog.get_logger(__name__)

# Rótulo humano de cada tipo de material (Addon de Livros)
MATERIAL_LABELS = {"lesson": "aula", "book": "livro", "workbook": "apostila"}


class ReviewQuiz(BaseModel):
    question: str = Field(description="Pergunta clara sobre o conteúdo revisado")
    alternatives: list[QuizAlternative] = Field(
        description="Lista de 3 alternativas (A, B, C)"
    )
    correct_answer: str = Field(description="ID da alternativa correta (A, B ou C)")
    explanation: str = Field(
        description="Explicação pedagógica de por que a resposta está correta. NUNCA cite a letra da alternativa (A, B, C) neste texto."
    )


class ReviewOutput(BaseModel):
    summary: str = Field(
        description="Resumo pedagógico dos pontos-chave da aula, em formato conversacional (máx 800 chars)"
    )
    key_points: list[str] = Field(
        description="Lista de 3 a 5 pontos-chave extraídos do conteúdo"
    )
    quiz: ReviewQuiz = Field(
        description="Quiz de múltipla escolha baseado no resumo (3 alternativas)"
    )
    topic: Optional[str] = None
    # Id da questão no Banco de Questões (QuizBank). Os chamadores repassam
    # este id ao send_quiz_to_student para registrar o consumo por aluno em
    # PendingQuizzes.quiz_bank_id (dedupe + analytics do /acertos).
    quiz_bank_id: Optional[str] = None
    # Vertical de Idiomas: versão COMPLETA da revisão na LÍNGUA-ALVO, p/ virar
    # áudio (a turma iniciante lê em pt-BR e ouve a tradução completa). Vazio
    # quando o próprio `summary` já está na língua-alvo (níveis avançados).
    audio_script: Optional[str] = None


# Lentes Pedagógicas customizadas para o Resumo Agendado
REVIEW_PERSPECTIVES = [
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas focado em um detalhe técnico minucioso",
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas sobre uma situação hipotética do mundo real",
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas sobre uma exceção à regra ou limitação",
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas explorando a definição do conceito central"
]

# Lentes do check-in de leitura (livro/apostila): questão curta sobre o TRECHO.
READING_PERSPECTIVES = [
    "A questão deve cobrar um detalhe técnico específico do trecho lido",
    "A questão deve aplicar o conceito do trecho a uma situação prática",
    "A questão deve cobrar a definição do conceito central do trecho",
    "A questão deve cobrar uma exceção ou limitação apresentada no trecho",
]

# Lentes de Flashcards (Fase 6.2): pares P/R curtos estilo Anki. Entram no
# MESMO QuizBank — e por isso herdam de graça a Revisão Espaçada da Fase 4
# (flashcard sem agendamento de repetição é truque; com ele, é retenção).
FLASHCARD_PERSPECTIVES = [
    "Formato FLASHCARD estilo Anki: pergunta direta e curtíssima (1 frase), alternativas de no máximo 6 palavras",
    "Formato FLASHCARD estilo Anki: peça o termo/conceito que corresponde à definição dada, alternativas de 1-3 palavras",
]

# F1 — Listening: cenários de diálogo do "áudio do dia". A variedade de cenário
# (somada à rotação de tópico/seção) faz o QuizBank crescer com áudios distintos.
LISTENING_PERSPECTIVES = [
    "Diálogo do cotidiano (ex.: pedir informação, conversa entre colegas)",
    "Diálogo de serviço (ex.: restaurante, loja, recepção, telefone)",
    "Diálogo de opinião curta sobre o tema da seção",
    "Diálogo de viagem (ex.: aeroporto, hotel, direções)",
]

# F2 — Vocabulário: ângulos para variar a palavra/expressão sorteada do material.
VOCAB_PERSPECTIVES = [
    "Priorize um substantivo ou verbo central do trecho",
    "Priorize uma expressão idiomática ou colocação útil",
    "Priorize um conectivo/advérbio frequente no trecho",
    "Priorize um falso cognato ou palavra que costuma confundir",
]

# F8 — Exam prep: estilos de questão TOEFL/IELTS.
EXAM_PREP_PERSPECTIVES = [
    "Compreensão de leitura (reading): inferência sobre um trecho",
    "Completar lacuna (gap-fill) com a palavra/colocação acadêmica correta",
    "Vocabulário acadêmico: sinônimo/uso no contexto",
    "Estrutura/gramática formal típica de prova",
]


def listening_directives(course_name: str) -> dict[str, str]:
    """Resolve as diretrizes de idioma do 'áudio do dia' pelo NOME do curso (§1.4)
    + defaults de CEFR/L1. Puro (lê só settings/regex) — testável.

    Retorna o ISO do alvo (`target_language`) e os nomes legíveis para o prompt.
    Se o nome não casar como curso de idioma, cai no DEFAULT_TARGET_LANGUAGE.
    """
    spec = detect_language_course(course_name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
    target = spec.target_language if spec else settings.DEFAULT_TARGET_LANGUAGE
    return {
        "target_language": target,
        "target_language_name": language_name(target),
        "cefr_level": settings.DEFAULT_CEFR_LEVEL,
        "native_language_name": language_name(settings.DEFAULT_NATIVE_LANGUAGE),
    }


def choose_topic_adaptive(available_topics: list[str], topic_stats: dict[str, tuple[int, int]]) -> str:
    """H3.2 — Dificuldade adaptativa: prioriza tópicos onde o aluno tem
    histórico FRACO (acerto < 70% com pelo menos 1 resposta). Sem histórico
    fraco, sorteio uniforme (comportamento clássico). Pura, testável.

    A rotação de tópicos por ciclo (used_topics) continua valendo — a
    priorização só reordena DENTRO do ciclo, nunca repete tópico.
    """
    weak = [
        t for t in available_topics
        if t in topic_stats
        and topic_stats[t][1] >= 1
        and (topic_stats[t][0] / topic_stats[t][1]) < 0.7
    ]
    return random.choice(weak) if weak else random.choice(available_topics)


def _shuffle_alternatives(quiz: ReviewQuiz) -> None:
    """Randomização algorítmica das alternativas (evita o viés do LLM de
    sempre colocar a correta em B). Reaplicada a CADA entrega — inclusive nos
    Cache Hits — para que dois colegas que recebam a mesma questão do banco
    não vejam a resposta correta na mesma letra (anti-cola).
    """
    if not quiz or not quiz.alternatives:
        return
    correct_id = quiz.correct_answer
    correct_text = next((a.text for a in quiz.alternatives if a.id == correct_id), None)
    if not correct_text:
        return

    random.shuffle(quiz.alternatives)
    new_ids = ["A", "B", "C", "D", "E"]
    for i, alt in enumerate(quiz.alternatives):
        alt.id = new_ids[i]
        if alt.text == correct_text:
            quiz.correct_answer = alt.id


def _review_from_bank_row(row: dict) -> Optional[ReviewOutput]:
    """Reidrata um ReviewOutput a partir de uma linha do QuizBank (Cache Hit)."""
    try:
        alts_raw = json.loads(row["alternatives"]) if row.get("alternatives") else []
        quiz = ReviewQuiz(
            question=row["question_text"],
            alternatives=[QuizAlternative(id=a["id"], text=a["text"]) for a in alts_raw],
            correct_answer=row["correct_answer"],
            explanation=row["explanation"],
        )
        _shuffle_alternatives(quiz)

        key_points = json.loads(row["key_points"]) if row.get("key_points") else []
        review = ReviewOutput(
            summary=row.get("summary") or "",
            key_points=key_points,
            quiz=quiz,
            topic=row.get("topic"),
        )
        review.quiz_bank_id = row["id"]
        review.audio_script = row.get("audio_script")  # áudio na língua-alvo (vocab/idioma)
        return review
    except Exception as e:
        logger.error("quiz_bank_row_parse_failed", quiz_bank_id=row.get("id"), error=str(e)[:300])
        return None


async def _retrieve_lesson_content(
    teacher_id: str,
    course_id: str,
    subject_name: Optional[str],
    current_lesson_number: int,
    material_type: str = "lesson",
) -> str:
    """Recupera os chunks mais representativos da aula/seção no Qdrant (RAG).
    Chamado de forma LAZY: apenas quando realmente precisamos do LLM.

    Para 'lesson' o filtro de material fica DESLIGADO (None): vetores legados
    de aulas não têm o campo material_type no payload e sumiriam da busca.
    Para 'book'/'workbook' o filtro é obrigatório (vetores novos, com payload).
    """
    material_label = MATERIAL_LABELS.get(material_type, "aula")
    return await retrieve_context(
        teacher_id=teacher_id,
        course_id=course_id,
        subject_name=subject_name,
        current_lesson_number=current_lesson_number,
        query=f"resumo principal da {material_label} {current_lesson_number}",
        top_k=settings.RAG_TOP_K * 2,  # More chunks for comprehensive review
        score_threshold=settings.RAG_SCORE_THRESHOLD,
        material_type=None if material_type == "lesson" else material_type,
    )


async def generate_review(
    teacher_id: str,
    teacher_name: str,
    teacher_subject: str,
    student_id: str,
    student_name: str,
    course_id: str,
    course_name: str,
    subject_name: str | None,
    current_lesson_number: int,
    lesson_label: str,
    subject_id: str | None = None,
    from_bank: bool = True,
    material_type: str = "lesson",
    perspectives: Optional[list[str]] = None,
    retrieval_material_type: Optional[str] = None,
) -> Optional[ReviewOutput]:
    """Gera (ou recupera do QuizBank) um pacote de revisão + quiz.

    Args:
        subject_id: id da Disciplina (escopo do cache). Se None, é resolvido a
            partir de (course_id, subject_name).
        from_bank: quando True (fluxo individual de revisão/quiz), tenta o
            Cache Hit antes de chamar o LLM. Quando False (ex.: broadcast
            /quiz do professor, que precisa de uma questão nova para a turma
            inteira), pula a LEITURA do cache — mas o pacote gerado é SEMPRE
            salvo no banco, enriquecendo o acervo para os fluxos individuais.
        material_type: 'lesson' (default, fluxo clássico) | 'book' | 'workbook'
            (Addon de Livros). Para materiais densos, current_lesson_number é o
            CAPÍTULO/SEÇÃO, o prompt vira um check-in de leitura (guia curto +
            quiz sobre o trecho específico) e todo o cache (tópicos Redis +
            QuizBank) fica isolado por material.
        perspectives: pool de Lentes Pedagógicas a sortear no Cache Miss.
            Default: REVIEW_PERSPECTIVES ('lesson') ou READING_PERSPECTIVES
            (livro/apostila). O /flashcards injeta FLASHCARD_PERSPECTIVES.

    Returns None if no content is found for the lesson in Qdrant.
    """
    # LGPD P0.1 (minimização): só o primeiro nome do aluno entra nos prompts
    # de revisão/check-in — choke point único para todos os chamadores.
    from src.shared.privacy import llm_safe_name
    student_name = llm_safe_name(student_name)

    from src.cache.topic_cache import (
        get_cached_topics,
        set_cached_topics,
        get_student_used_topics,
        add_student_used_topic,
        clear_student_used_topics,
    )

    # 0. Resolve o escopo de Disciplina do cache, se o chamador não forneceu
    if subject_id is None and subject_name:
        subject_id = await quiz_bank.resolve_subject_id(course_id, subject_name)

    llm = None                      # lazy: só instancia se houver Cache Miss
    lesson_content: Optional[str] = None  # lazy: RAG só roda se necessário

    # F1 (Listening): o cache/prompt usam material_type='listening' (escopo
    # isolado), mas a RECUPERAÇÃO (RAG) lê o material REAL da seção — não há
    # vetores 'listening' indexados. retrieval_material_type permite esse desacople.
    retrieval_mt = retrieval_material_type or material_type

    # Pool de lentes: explícito (flashcards) > exam > vocab > listening > leitura > aulas.
    if perspectives is None:
        if material_type == "exam":
            perspectives = EXAM_PREP_PERSPECTIVES
        elif material_type == "vocab":
            perspectives = VOCAB_PERSPECTIVES
        elif material_type == "listening":
            perspectives = LISTENING_PERSPECTIVES
        elif material_type == "lesson":
            perspectives = REVIEW_PERSPECTIVES
        else:
            perspectives = READING_PERSPECTIVES

    # ── ETAPA 1: Tópicos da aula/seção (Redis) ──────────────────────────────
    # Cache global da turma. Se a lista não existir (primeiro aluno da turma a
    # pedir o quiz), o LLM analisa o RAG da aula e cria a lista no Redis.
    topics = await get_cached_topics(course_id, current_lesson_number, subject_name, material_type)

    if not topics:
        lesson_content = await _retrieve_lesson_content(
            teacher_id, course_id, subject_name, current_lesson_number, retrieval_mt
        )
        if not lesson_content:
            logger.warning(
                "No content found in Qdrant for review generation",
                teacher_id=teacher_id,
                course_id=course_id,
                current_lesson_number=current_lesson_number,
                material_type=material_type,
            )
            return None

        llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
        topic_prompt = TOPIC_EXTRACTION_PROMPT.format(context=lesson_content)
        topics_response = await llm.agenerate(prompt=topic_prompt)

        topics = [t.strip() for t in topics_response.split(",") if t.strip()]
        if not topics:
            topics = ["Conceito Geral da Aula"]

        await set_cached_topics(course_id, current_lesson_number, topics, subject_name, material_type)

    # ── ETAPA 2: Sorteio do tópico ("pop" por aluno) ────────────────────────
    # Restrição individual: o aluno não repete tópico até exaurir o ciclo.
    used_topics = await get_student_used_topics(
        student_id, course_id, current_lesson_number, subject_name, material_type
    )
    available_topics = [t for t in topics if t not in used_topics]

    # Se o aluno já consumiu TODOS os tópicos desta aula, resetamos a lista dele!
    if not available_topics:
        logger.info(
            "Aluno exauriu os tópicos desta aula. Resetando ciclo.",
            student_id=student_id,
            lesson_number=current_lesson_number,
        )
        await clear_student_used_topics(student_id, course_id, current_lesson_number, subject_name, material_type)
        available_topics = topics  # Restaura tudo

    # H3.2 — Dificuldade adaptativa (flag): tópicos com histórico fraco do
    # aluno têm prioridade na rotação. Best-effort: falha → sorteio clássico.
    chosen_topic = None
    if settings.ADAPTIVE_TOPIC_PRIORITY and student_id != "broadcast":
        try:
            topic_stats = await quiz_bank.get_student_topic_stats(
                student_id, course_id, subject_id, current_lesson_number, material_type
            )
            if topic_stats:
                chosen_topic = choose_topic_adaptive(available_topics, topic_stats)
        except Exception as e:
            logger.warning("adaptive_topic_priority_failed", error=str(e)[:200])

    if chosen_topic is None:
        chosen_topic = random.choice(available_topics)

    # ── ETAPA 3: CACHE HIT (QuizBank) ───────────────────────────────────────
    # Existe uma questão desse tópico (com QUALQUER lente) que o aluno ainda
    # não recebeu? Envia direto do banco: zero LLM, zero RAG, custo $0.
    if from_bank:
        bank_row = None
        try:
            bank_row = await quiz_bank.find_unseen_quiz(
                student_id=student_id,
                course_id=course_id,
                subject_id=subject_id,
                lesson_number=current_lesson_number,
                topic=chosen_topic,
                material_type=material_type,
            )
        except Exception as e:
            # Falha no cache NUNCA derruba o fluxo — degrada para geração.
            logger.error("quiz_bank_lookup_failed", error=str(e)[:300])

        if bank_row:
            review = _review_from_bank_row(bank_row)
            if review:
                await add_student_used_topic(
                    student_id, course_id, current_lesson_number, chosen_topic, subject_name, material_type
                )
                logger.info(
                    "quiz_bank_cache_hit",
                    student_id=student_id,
                    course_id=course_id,
                    lesson_number=current_lesson_number,
                    material_type=material_type,
                    topic=chosen_topic[:60],
                    quiz_bank_id=review.quiz_bank_id,
                )
                return review

    # ── ETAPA 4: CACHE MISS — gera com uma Lente Pedagógica inédita ────────
    if lesson_content is None:
        lesson_content = await _retrieve_lesson_content(
            teacher_id, course_id, subject_name, current_lesson_number, retrieval_mt
        )
        if not lesson_content:
            logger.warning(
                "No content found in Qdrant for review generation",
                teacher_id=teacher_id,
                course_id=course_id,
                current_lesson_number=current_lesson_number,
                material_type=material_type,
            )
            return None

    if llm is None:
        llm = get_provider(settings.DEFAULT_LLM_PROVIDER)

    # Sorteia uma lente que ainda NÃO exista no banco para esse tópico
    # (Tópico 1 + Lente A já existe? Então nasce Tópico 1 + Lente B).
    used_lenses: list[str] = []
    try:
        used_lenses = await quiz_bank.get_used_lenses(
            course_id, subject_id, current_lesson_number, chosen_topic, material_type
        )
    except Exception as e:
        logger.warning("quiz_bank_lenses_lookup_failed", error=str(e)[:200])

    available_lenses = [p for p in perspectives if p not in used_lenses]
    if not available_lenses:
        # Todas as lentes do tópico já foram materializadas e o aluno já viu
        # todas: gera uma nova variação com uma lente repetida (o texto da
        # questão ainda assim será diferente, e o banco continua crescendo).
        available_lenses = list(perspectives)

    chosen_perspective = random.choice(available_lenses)

    # Grava o tópico recém-sorteado no cache do aluno para ele não recebê-lo de novo
    await add_student_used_topic(
        student_id, course_id, current_lesson_number, chosen_topic, subject_name, material_type
    )

    # Geração do Resumo e Quiz (em 1 só chamada usando Structured Outputs).
    # Para livro/apostila o prompt é o de CHECK-IN DE LEITURA: guia curto
    # (proteção autoral) + quiz sobre o trecho específico da seção (Fase 3).
    if material_type == "exam":
        # F8 — Exam prep: questão estilo TOEFL/IELTS na língua-alvo.
        dirs = listening_directives(course_name)
        prompt = EXAM_PREP_PROMPT.format(
            target_language_name=dirs["target_language_name"],
            cefr_level=dirs["cefr_level"],
            student_name=student_name,
            course_name=course_name,
            lesson_label=lesson_label,
            topic=chosen_topic,
            perspective=chosen_perspective,
            lesson_content=lesson_content,
        )
    elif material_type == "vocab":
        # F2 — Vocabulário: flashcard de palavra-alvo (significado em L1) + áudio.
        dirs = listening_directives(course_name)
        prompt = VOCAB_GENERATION_PROMPT.format(
            target_language_name=dirs["target_language_name"],
            cefr_level=dirs["cefr_level"],
            native_language_name=dirs["native_language_name"],
            student_name=student_name,
            course_name=course_name,
            lesson_label=lesson_label,
            topic=chosen_topic,
            perspective=chosen_perspective,
            lesson_content=lesson_content,
        )
    elif material_type == "listening":
        # F1 — "áudio do dia": diálogo na língua-alvo + pergunta de compreensão.
        dirs = listening_directives(course_name)
        prompt = LISTENING_PROMPT_TEMPLATE.format(
            target_language_name=dirs["target_language_name"],
            cefr_level=dirs["cefr_level"],
            native_language_name=dirs["native_language_name"],
            student_name=student_name,
            course_name=course_name,
            section_label=lesson_label,
            topic=chosen_topic,
            perspective=chosen_perspective,
            lesson_content=lesson_content,
        )
    elif material_type == "lesson":
        prompt = REVIEW_PROMPT_TEMPLATE.format(
            teacher_name=teacher_name,
            teacher_subject=teacher_subject,
            student_name=student_name,
            course_name=course_name,
            lesson_label=lesson_label,
            topic=chosen_topic,
            perspective=chosen_perspective,
            lesson_content=lesson_content,
        )
    else:
        material_label = MATERIAL_LABELS.get(material_type, "livro")
        prompt = READING_REVIEW_PROMPT_TEMPLATE.format(
            teacher_name=teacher_name,
            teacher_subject=teacher_subject,
            student_name=student_name,
            course_name=course_name,
            section_label=lesson_label,
            material_label=material_label,
            topic=chosen_topic,
            perspective=chosen_perspective,
            lesson_content=lesson_content,
        )

    try:
        review = cast(ReviewOutput, await llm.agenerate_structured(prompt=prompt, schema=ReviewOutput))
        review.topic = chosen_topic

        # Randomização algorítmica (mesma lógica de sempre, agora num helper)
        _shuffle_alternatives(review.quiz)

        # Persiste o pacote no QuizBank — mesmo no broadcast (from_bank=False):
        # a turma toda passa a aproveitar nas próximas revisões individuais.
        try:
            review.quiz_bank_id = await quiz_bank.save_quiz_package(
                course_id=course_id,
                subject_id=subject_id,
                lesson_number=current_lesson_number,
                topic=chosen_topic,
                pedagogical_lens=chosen_perspective,
                question_text=review.quiz.question,
                alternatives=[{"id": a.id, "text": a.text} for a in review.quiz.alternatives],
                correct_answer=review.quiz.correct_answer,
                explanation=review.quiz.explanation,
                summary=review.summary,
                key_points=review.key_points,
                material_type=material_type,
                audio_script=review.audio_script,
            )
        except Exception as e:
            # Falha ao salvar no banco não bloqueia a entrega ao aluno.
            logger.error("quiz_bank_save_failed", error=str(e)[:300])

        logger.info(
            "Review generated successfully (quiz_bank cache miss)",
            teacher_id=teacher_id,
            course_id=course_id,
            current_lesson_number=current_lesson_number,
            material_type=material_type,
            topic=chosen_topic[:60],
            pedagogical_lens=chosen_perspective[:60],
            key_points_count=len(review.key_points),
            quiz_bank_id=review.quiz_bank_id,
        )
        return review
    except Exception as e:
        logger.error(
            "Failed to parse review output from LLM",
            error=str(e)[:500]
        )
        return None


async def synthesize_listening(text: str, target_language: str) -> bytes | None:
    """F1 — sintetiza o diálogo do 'áudio do dia' na língua-alvo via `get_tts()`.

    None = vertical/TTS desligados, sem provider utilizável, ou erro — o chamador
    degrada para texto (o áudio é SEMPRE camada extra, nunca substitui). Best-effort.
    """
    if not (settings.LANGUAGE_MODE_ENABLED and settings.TTS_ENABLED):
        return None
    from src.speech.factory import get_tts
    from src.speech.stt import to_locale
    from src.speech.tts import default_voice_for
    provider = get_tts()
    if provider is None:
        return None
    try:
        return await provider.synthesize(text, default_voice_for(target_language), to_locale(target_language))
    except Exception as e:
        logger.warning("listening_tts_failed", lang=target_language, error=str(e)[:200])
        return None


def class_language_context(
    course_name: str,
    is_language: bool,
    proficiency_level: str | None,
) -> tuple[LanguageDelivery | None, str | None]:
    """Resolve (delivery, idioma-alvo) de uma TURMA para a revisão/quiz de idioma.

    (None, None) ⇒ vertical off ou turma não-idioma → comportamento atual. Caso
    contrário, o alvo vem do NOME do curso (§1.4) / DEFAULT, e o `delivery` define
    a língua do texto (por nível) e o CEFR. Compartilhado por todos os fluxos
    (revisão individual, broadcast /quiz). Quase puro (lê só settings)."""
    if not (settings.LANGUAGE_MODE_ENABLED and is_language):
        return None, None
    spec = detect_language_course(course_name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
    target = spec.target_language if spec else settings.DEFAULT_TARGET_LANGUAGE
    delivery = language_delivery(True, proficiency_level, target, settings.DEFAULT_NATIVE_LANGUAGE)
    return delivery, target


async def generate_language_review(
    teacher_id: str,
    student_name: str,
    course_id: str,
    course_name: str,
    subject_name: str | None,
    current_lesson_number: int,
    lesson_label: str,
    delivery: LanguageDelivery,
    target_language: str,
    material_type: str = "lesson",
) -> Optional[ReviewOutput]:
    """Revisão de CURSO DE IDIOMA, com a língua do TEXTO por nível e a versão na
    língua-alvo p/ áudio (`audio_script`).

    Gerador DEDICADO e SEM cache de propósito: o QuizBank é escopado por
    curso/disciplina/aula/tópico (sem o nível), então cachear aqui colidiria
    entre turmas de níveis diferentes (texto em L1 vs alvo). A revisão é diária e
    roda na janela de 24h — 1 chamada de LLM é aceitável. Retorna None sem
    conteúdo (o chamador cai na revisão normal).
    """
    from src.shared.privacy import llm_safe_name
    student_name = llm_safe_name(student_name)

    # Listening tem fluxo próprio; aqui a recuperação lê sempre o material real.
    content = await _retrieve_lesson_content(
        teacher_id, course_id, subject_name, current_lesson_number,
        "lesson" if material_type == "listening" else material_type,
    )
    if not content:
        return None

    llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
    prompt = LANGUAGE_REVIEW_PROMPT_TEMPLATE.format(
        target_language_name=language_name(target_language),
        text_language_name=language_name(delivery.text_language),
        cefr_level=delivery.cefr_level,
        student_name=student_name,
        course_name=course_name,
        lesson_label=lesson_label,
        perspective=random.choice(REVIEW_PERSPECTIVES),
        lesson_content=content,
    )
    try:
        review = cast(ReviewOutput, await llm.agenerate_structured(prompt=prompt, schema=ReviewOutput))
        _shuffle_alternatives(review.quiz)
        return review
    except Exception as e:
        logger.error("language_review_generation_failed", error=str(e)[:300])
        return None
