"""
Text Splitter Adapter — Decouples LangChain chunking logic from business workers.

F1 (RAG de Alta Precisão) — Chunking Estrutural:
A causa nº 1 de erro em respostas técnicas é separar a "regra geral" da sua
"exceção" em chunks diferentes. Este módulo monta chunks respeitando a
estrutura do documento:

  - Parágrafos de CONTINUAÇÃO ("Exceção:", "Salvo", "Porém", "§", itens "a)",
    "-", "•"...) NUNCA abrem chunk novo — colam no buffer anterior mesmo
    estourando o limite, até um teto duro de 1.5× o chunk_size.
  - HEADINGS (blocos markdown do Azure DI) são fronteiras preferenciais e
    viram o metadado `section_title` de todos os chunks da seção.

Helpers puros (sem I/O) — cobertos por testes unitários.
"""

import re
from dataclasses import dataclass
from typing import Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

# Teto duro: um chunk pode estourar até 1.5× o limite para não separar a
# regra geral da sua exceção; acima disso o split recursivo assume.
GLUE_HARD_CAP_FACTOR = 1.5

# Conectivos de continuação em PT-BR: parágrafo iniciado por um deles pertence
# semanticamente ao parágrafo anterior (exceção/ressalva/observação da regra).
_CONTINUATION_WORDS = [
    r"exce[çc][ãa]o",
    r"exce[çc][õo]es",
    r"salvo\b",
    r"por[ée]m\b",
    r"entretanto\b",
    r"contudo\b",
    r"todavia\b",
    r"no\s+entanto\b",
    r"observa[çc][ãa]o",
    r"obs\.?\s*:",
    r"nota\s*:",
    r"par[áa]grafo\s+[úu]nico",
    r"§",
    r"importante\s*:",
    r"aten[çc][ãa]o\s*:",
]
_CONTINUATION_REGEX = re.compile(
    r"^\s*(?:" + "|".join(_CONTINUATION_WORDS) + r")", re.IGNORECASE
)

# Itens de lista/alíneas: "a)", "iv)", "1)", "-", "–", "•", "*" — continuam a
# enumeração iniciada no parágrafo anterior.
_LIST_ITEM_REGEX = re.compile(r"^\s*(?:[a-z]\)|[ivxlc]+\)|\d{1,2}\)|[-–•·*]\s)", re.IGNORECASE)

# Headings: blocos markdown ("# ...", "## ...") devolvidos pelo Azure DI
# (prebuilt-layout marca roles title/sectionHeading) ou seções numeradas
# curtas ("3.2 Equações do segundo grau") sem pontuação final de frase.
_MD_HEADING_REGEX = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_NUMBERED_HEADING_REGEX = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2}){0,3}\s*[-–.)]?\s+[^\n]{3,80}$")

# Fórmulas em DISPLAY (saída do parser ciente de matemática): $$...$$, \[...\] e
# ambientes \begin{env}...\end{env}. Estes spans são ATÔMICOS — uma integral ou
# matriz partida ao meio vira um vetor sem sentido e o RAG explica a premissa
# errada (cf. "Pipeline RAG para SaaS Educacional" §4.2). O chunking NUNCA corta
# dentro deles. Math INLINE ($x$, A_0) é curto e segue o fluxo normal — só o
# display, que cresce (matrizes, align), é protegido aqui.
_DISPLAY_MATH_REGEX = re.compile(
    r"\$\$.+?\$\$"                              # $$ ... $$
    r"|\\\[.+?\\\]"                             # \[ ... \]
    r"|\\begin\{(\w+\*?)\}.*?\\end\{\1\}",      # \begin{env} ... \end{env}
    re.DOTALL,
)


def contains_display_math(text: str) -> bool:
    """True se o texto contém ao menos uma fórmula em display (span atômico)."""
    return bool(_DISPLAY_MATH_REGEX.search(text or ""))


def is_math_block(paragraph: str) -> bool:
    """True se o parágrafo é PRINCIPALMENTE uma fórmula em display.

    Um bloco assim não abre chunk sozinho: pertence ao enunciado anterior
    ("considere a equação:" → fórmula → "onde x é..."). Separá-lo orfana a
    fórmula do seu contexto — o erro clássico de splitters de tamanho fixo.
    """
    if not paragraph:
        return False
    stripped = paragraph.strip()
    starts_display = (
        stripped.startswith("$$")
        or stripped.startswith(r"\[")
        or stripped.startswith(r"\begin{")
    )
    return starts_display and contains_display_math(stripped)


def should_glue_paragraph(prev_chunk: str, paragraph: str) -> bool:
    """True se `paragraph` NÃO pode abrir chunk novo (cola no buffer anterior).

    Regra↔exceção: um parágrafo iniciado por conectivo de continuação ou item
    de lista pertence ao contexto do parágrafo anterior — separá-los faz o RAG
    responder a regra geral sem a exceção (caso clássico de erro técnico).
    """
    if not prev_chunk or not prev_chunk.strip():
        return False
    if not paragraph or not paragraph.strip():
        return False
    stripped = paragraph.strip()
    # Fórmula em display nunca abre chunk sozinha — cola no enunciado anterior.
    if is_math_block(stripped):
        return True
    return bool(_CONTINUATION_REGEX.match(stripped) or _LIST_ITEM_REGEX.match(stripped))


def is_heading(paragraph: str) -> bool:
    """Detecta um bloco de heading (fronteira preferencial de chunk)."""
    if not paragraph:
        return False
    stripped = paragraph.strip()
    if _MD_HEADING_REGEX.match(stripped):
        return True
    # Seção numerada curta, em linha única, sem final de frase.
    if "\n" not in stripped and _NUMBERED_HEADING_REGEX.match(stripped):
        return not stripped.rstrip().endswith((".", ":", ";", ",", "!", "?"))
    return False


def heading_text(paragraph: str) -> str:
    """Extrai o texto limpo do heading (sem os '#' do markdown)."""
    stripped = (paragraph or "").strip()
    match = _MD_HEADING_REGEX.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def extract_heading_outline(paragraphs: list[str], max_items: int = 30) -> list[str]:
    """Sumário de headings do documento — alimenta o `document_excerpt` do
    enriquecimento contextual (F2)."""
    outline = []
    for para in paragraphs:
        if is_heading(para):
            outline.append(heading_text(para))
            if len(outline) >= max_items:
                break
    return outline


@dataclass
class StructuredChunk:
    """Chunk com metadado estrutural (seção de origem)."""
    text: str
    section_title: Optional[str] = None


def split_document_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200, separators: list[str] | None = None) -> list[str]:
    """Splits a long text into overlapping chunks suitable for embedding."""
    if separators is None:
        separators = ["\n\n", "\n", " ", ""]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=separators,
        length_function=len,
        is_separator_regex=False,
    )
    return splitter.split_text(text)


def split_protecting_math(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: list[str] | None = None,
) -> list[str]:
    """Fatia um texto longo SEM nunca cortar dentro de uma fórmula em display.

    A prosa entre fórmulas segue o split recursivo normal; cada fórmula
    (`$$...$$`, `\\[...\\]`, `\\begin..\\end`) é um átomo indivisível — se
    sozinha já estoura o `chunk_size`, vira um chunk inteiro (fórmula íntegra
    vale mais que fórmula no tamanho certo, porém partida).

    Sem fórmula em display, delega ao `split_document_text` — saída byte-a-byte
    idêntica ao caminho atual (preserva o comportamento de documentos de prosa).
    """
    if not contains_display_math(text):
        return split_document_text(text, chunk_size, chunk_overlap, separators)

    # 1. Quebra em átomos, alternando prosa | fórmula, na ordem original.
    atoms: list[tuple[str, str]] = []  # (kind, text); kind ∈ {"text", "math"}
    last = 0
    for m in _DISPLAY_MATH_REGEX.finditer(text):
        if m.start() > last:
            atoms.append(("text", text[last:m.start()]))
        atoms.append(("math", m.group(0)))
        last = m.end()
    if last < len(text):
        atoms.append(("text", text[last:]))

    # 2. Empacota os átomos em chunks ≤ chunk_size; prosa longa é sub-fatiada,
    #    a fórmula nunca (átomo). A fórmula gruda na prosa adjacente quando cabe.
    chunks: list[str] = []
    buffer = ""

    def flush_buffer() -> None:
        nonlocal buffer
        if buffer.strip():
            chunks.append(buffer.strip())
        buffer = ""

    for kind, atom in atoms:
        atom = atom.strip("\n")
        if not atom.strip():
            continue
        if kind == "math":
            if buffer and len(buffer) + len(atom) + 1 > chunk_size:
                flush_buffer()
            buffer = f"{buffer}\n{atom}".strip() if buffer else atom
            if len(buffer) >= chunk_size:  # fórmula grande fecha o chunk
                flush_buffer()
        elif len(atom) > chunk_size:
            flush_buffer()
            pieces = split_document_text(atom, chunk_size, chunk_overlap, separators)
            # O último pedaço da prosa segue acumulável com a fórmula seguinte.
            chunks.extend(pieces[:-1])
            buffer = pieces[-1] if pieces else ""
        elif buffer and len(buffer) + len(atom) + 1 > chunk_size:
            flush_buffer()
            buffer = atom
        else:
            buffer = f"{buffer}\n{atom}".strip() if buffer else atom

    flush_buffer()
    return chunks


def assemble_structural_chunks(
    paragraphs: list[str],
    chunk_size: int,
    chunk_overlap: int,
) -> list[StructuredChunk]:
    """Monta chunks estruturais a partir dos parágrafos do documento.

    Regras (nesta ordem):
      1. Heading → fecha o buffer atual, vira `section_title` da seção e abre
         o próximo chunk (o texto do heading fica no corpo, ajudando o braço
         lexical da busca híbrida).
      2. Parágrafo de continuação (`should_glue_paragraph`) → cola no buffer
         mesmo estourando o chunk_size, até o teto duro de 1.5×.
      3. Parágrafo normal → cola se couber em chunk_size; senão fecha o
         buffer e abre chunk novo.

    Buffers que mesmo assim excedem o teto duro passam pelo split recursivo
    (preservando o section_title da seção).
    """
    hard_cap = int(chunk_size * GLUE_HARD_CAP_FACTOR)
    chunks: list[StructuredChunk] = []
    buffer = ""
    current_title: Optional[str] = None

    def flush():
        nonlocal buffer
        content = buffer.strip()
        buffer = ""
        if not content:
            return
        if len(content) > hard_cap:
            for piece in split_protecting_math(
                content, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""],
            ):
                chunks.append(StructuredChunk(text=piece, section_title=current_title))
        else:
            chunks.append(StructuredChunk(text=content, section_title=current_title))

    for para in paragraphs:
        para = (para or "").strip()
        if not para:
            continue

        if is_heading(para):
            flush()
            current_title = heading_text(para)
            buffer = current_title
            continue

        candidate_len = len(buffer) + len(para) + 2
        if should_glue_paragraph(buffer, para):
            if candidate_len <= hard_cap:
                buffer = f"{buffer}\n\n{para}"
            else:
                # Teto duro estourado: fecha e segue (o split recursivo do
                # flush preservaria o par, mas aqui o buffer já está íntegro).
                flush()
                buffer = para
        elif candidate_len <= chunk_size:
            buffer = f"{buffer}\n\n{para}".strip()
        else:
            flush()
            buffer = para

    flush()
    return chunks
