"""Carga inicial em lote via Mistral Batch OCR (−50%), reusando o funil oficial.

A ideia: o Batch é só uma forma OFFLINE e mais barata de produzir os mesmos
`raw_paragraphs` que um `IDocumentParser.parse()` produziria — depois disso, a
ingestão reusa EXATAMENTE o pipeline do worker (`ingest_paragraphs`): chunking
estrutural (F1) → enriquecimento contextual (F2) → embedding híbrido (F3) →
upsert no Qdrant com o payload de escopo.

Duas fases:

  submit  — lista os materiais pela MESMA fonte do worker (Drive), resolve
            course_id/teacher_id no Postgres, baixa + base64 dos PDFs, gera o
            JSONL (custom_id = file_id do Drive) e cria o Batch Job de OCR.
            Grava um MANIFESTO job→metadados (o resultado do Batch só devolve o
            custom_id; os metadados de escopo ficam aqui).

  process — baixa o resultado do job, reconstrói os `raw_paragraphs` com o MESMO
            enriquecimento do MistralOCRParser (contexto + descrição de imagens
            via Gemini), chama `ingest_paragraphs(...)` e marca
            file_sync_state='concluído' — senão o poller do Drive re-enfileira e
            re-parseia tudo via Azure DI (pagando caro), desfazendo a economia.

Roda contra PROD (precisa de Qdrant + embedder + Drive + Postgres):

    ENV_FILE=.env.prod python scripts/bulk_ingest_mistral.py submit
    ENV_FILE=.env.prod python scripts/bulk_ingest_mistral.py process <job_id>

Só PDFs entram no Batch (o OCR é o custo que se quer cortar). Markdown/TXT e
Office seguem pelo sync normal do worker (texto não tem custo de OCR).
"""

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

# Garante que a raiz do projeto esteja no sys.path ANTES de importar src.*
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mistralai.client import Mistral  # noqa: E402

from src.shared.config import settings  # noqa: E402
from src.db.database import AsyncSessionLocal  # noqa: E402
from src.drive.factory import get_material_source  # noqa: E402
from src.parsing.mistral_parser import MistralOCRParser  # noqa: E402
from src.parsing.pdf_utils import split_pdf_for_ocr  # noqa: E402
from src.workers.ingestion_worker import ingest_paragraphs, invalidate_quiz_cache_for_lesson  # noqa: E402

JSONL_PATH = Path("mistral_bulk_request.jsonl")


def manifest_path(job_id: str) -> Path:
    return Path(f"mistral_bulk_manifest_{job_id}.json")


def make_custom_id(file_id: str, part_idx: int, n_parts: int) -> str:
    """custom_id do Batch = file_id + posição da parte (PDFs grandes viram N
    requests). Formato `{file_id}::p{i}of{n}` — o file_id do Drive não contém `::`."""
    return f"{file_id}::p{part_idx}of{n_parts}"


def parse_custom_id(custom_id: str) -> tuple[str, int, int]:
    """Inverte make_custom_id → (file_id, part_idx, n_parts). Tolera o formato
    antigo (só o file_id) tratando-o como parte única."""
    if "::p" in custom_id:
        file_id, rest = custom_id.rsplit("::p", 1)
        idx_s, _, tot_s = rest.partition("of")
        if idx_s.isdigit() and tot_s.isdigit():
            return file_id, int(idx_s), int(tot_s)
    return custom_id, 0, 1


def make_client() -> Mistral:
    if not settings.MISTRAL_API_KEY:
        print("Erro: MISTRAL_API_KEY não configurada. Rode com ENV_FILE=.env.prod (chaves de prod).")
        sys.exit(1)
    return Mistral(api_key=settings.MISTRAL_API_KEY)


async def _resolve_course(session, course_name: str):
    """Mesmo lookup do worker: nome do curso → (course_id, teacher_id)."""
    row = (await session.execute(
        text('SELECT id, teacher_id FROM "Courses" WHERE name = :name LIMIT 1'),
        {"name": course_name},
    )).fetchone()
    return (row[0], row[1]) if row else (None, None)


# ─────────────────────────── FASE 1: submit ────────────────────────────────

async def submit_batch(folder_id: str | None):
    client = make_client()
    folder_id = folder_id or settings.GOOGLE_DRIVE_FOLDER_ID
    if not folder_id:
        print("Erro: passe --folder_id ou configure GOOGLE_DRIVE_FOLDER_ID.")
        return

    source = get_material_source()
    print(f"Listando materiais da fonte '{settings.MATERIAL_SOURCE_BACKEND}' (raiz {folder_id})...")
    materials = await source.list_materials(folder_id)
    print(f"{len(materials)} materiais encontrados.\n")

    manifest: list[dict] = []
    skipped = 0

    async with AsyncSessionLocal() as session:
        with open(JSONL_PATH, "w", encoding="utf-8") as f:
            for m in materials:
                is_pdf = "pdf" in (m.mime_type or "").lower() or m.name.lower().endswith(".pdf")
                if not is_pdf:
                    print(f"  - pula (não-PDF, vai pelo sync normal): {m.name}")
                    skipped += 1
                    continue
                if not m.course_name or not m.subject_name:
                    print(f"  - pula (sem Curso/Disciplina na hierarquia): {m.name}")
                    skipped += 1
                    continue

                course_id, teacher_id = await _resolve_course(session, m.course_name)
                if not course_id:
                    print(f"  - pula (curso '{m.course_name}' não está no banco): {m.name}")
                    skipped += 1
                    continue

                try:
                    file_bytes = await source.download(m.id)
                except Exception as e:
                    print(f"  - ERRO ao baixar {m.name}: {e}")
                    skipped += 1
                    continue

                # Respeita o teto do OCR da Mistral (≤1000 págs / ~50 MB): PDF
                # dentro do limite vira 1 request; PDF grande vira N requests
                # (reassembladas por file_id no process). Pedaço intacto = caso comum.
                chunks = split_pdf_for_ocr(file_bytes)
                for part_idx, chunk_bytes in enumerate(chunks):
                    b64 = base64.b64encode(chunk_bytes).decode("utf-8")
                    request = {
                        # custom_id = file_id + parte → liga o resultado de volta aos
                        # metadados (manifesto) e à ordem das partes do mesmo arquivo.
                        "custom_id": make_custom_id(m.id, part_idx, len(chunks)),
                        "body": {
                            "document": {
                                "type": "document_url",
                                "document_url": f"data:application/pdf;base64,{b64}",
                            },
                            "include_image_base64": True,  # Gemini descreve as figuras no process
                            # Isola cabeçalho/rodapé corridos (mesmo flag do parser online).
                            "extract_header": settings.MISTRAL_OCR_EXTRACT_HEADER_FOOTER,
                            "extract_footer": settings.MISTRAL_OCR_EXTRACT_HEADER_FOOTER,
                        },
                    }
                    f.write(json.dumps(request) + "\n")

                manifest.append({
                    "file_id": m.id,
                    "file_name": m.name,
                    "mime_type": m.mime_type,
                    "teacher_id": teacher_id,
                    "course_id": course_id,
                    "subject_name": m.subject_name,
                    "lesson_number": m.lesson_number,
                    "material_type": m.material_type,
                    "n_parts": len(chunks),
                })
                extra = f"  ({len(chunks)} partes — PDF grande)" if len(chunks) > 1 else ""
                print(f"  ✓ {m.name}  [{m.course_name} > {m.subject_name} > aula {m.lesson_number}]{extra}")

    if not manifest:
        print("\nNenhum PDF elegível para o Batch. Nada a enviar.")
        return

    size_mb = JSONL_PATH.stat().st_size / (1024 * 1024)
    print(f"\nJSONL: {len(manifest)} PDFs, {size_mb:.1f} MB ({skipped} pulados).")
    if size_mb > 500:
        print("⚠  Arquivo grande — se o upload falhar, quebre a carga por pasta (--folder_id).")

    print("Subindo o JSONL para a Mistral...")
    with open(JSONL_PATH, "rb") as f:
        uploaded = client.files.upload(
            file={"file_name": JSONL_PATH.name, "content": f.read()},
            purpose="batch",
        )
    print(f"Upload OK. File ID: {uploaded.id}")

    print("Criando o Batch Job de OCR (−50%)...")
    job = client.batch.jobs.create(
        input_files=[uploaded.id],
        model="mistral-ocr-latest",
        endpoint="/v1/ocr",
        metadata={"job_type": "bulk_ingest"},
    )

    mpath = manifest_path(job.id)
    mpath.write_text(
        json.dumps({"job_id": job.id, "folder_id": folder_id, "files": manifest}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n✅ JOB CRIADO: {job.id}")
    print(f"📄 Manifesto: {mpath.name}  (NÃO apague — o process precisa dele para os metadados)")
    print("\nO Batch é assíncrono (pode levar minutos/horas). Quando concluir:")
    print(f"\n    ENV_FILE=.env.prod python scripts/bulk_ingest_mistral.py process {job.id}\n")


# ─────────────────────────── FASE 2: process ───────────────────────────────

def _dump_error_file(client: Mistral, error_file_id: str):
    try:
        resp = client.files.download(file_id=error_file_id)
        p = Path("mistral_bulk_errors.jsonl")
        p.write_bytes(resp.content)
        print(f"⚠  Linhas com erro no OCR salvas em {p.name} (inspecione quais PDFs falharam).")
    except Exception as e:
        print(f"(não consegui baixar o error_file: {e})")


async def _mark_concluido(meta: dict):
    """Marca o arquivo como 'concluído' em file_sync_state — impede o poller do
    Drive de re-enfileirar e re-parsear via Azure DI (custo)."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO file_sync_state (file_id, teacher_id, status, attempts, last_synced)
                VALUES (:fid, :tid, 'concluído', 0, CURRENT_TIMESTAMP)
                ON CONFLICT(file_id) DO UPDATE SET status='concluído', attempts=0, last_synced=CURRENT_TIMESTAMP
            """),
            {"fid": meta["file_id"], "tid": meta["teacher_id"]},
        )
        await session.commit()


async def _ingest_file(parser: MistralOCRParser, meta: dict, parts_pages: list[list[dict]]) -> bool:
    """Enriquece (por parte, com numeração de página contínua) e sobe os vetores
    de UM arquivo. `parts_pages` = páginas de cada parte, já na ordem correta."""
    name = meta["file_name"]
    total_pages = sum(len(p) for p in parts_pages)
    n = len(parts_pages)
    label = f"{name} ({total_pages} págs" + (f", {n} partes)" if n > 1 else ")")
    print(f"  → {label}: enriquecendo + ingerindo...")

    raw_paragraphs: list[str] = []
    page_offset = 0
    for pages in parts_pages:
        raw_paragraphs += await parser.enrich_pages_to_paragraphs(pages, start_page=page_offset + 1)
        page_offset += len(pages)
    full_text = "\n\n".join(raw_paragraphs)

    success = await ingest_paragraphs(
        raw_paragraphs=raw_paragraphs,
        full_text=full_text,
        file_id=meta["file_id"],
        file_name=name,
        teacher_id=meta["teacher_id"],
        course_id=meta["course_id"],
        subject_name=meta["subject_name"],
        lesson_number=meta["lesson_number"],
        material_type=meta["material_type"],
    )
    if not success:
        print("    ✗ ingestão falhou (ver logs estruturados)")
        return False

    await _mark_concluido(meta)
    # Paridade com o worker: invalida QuizBank/tópicos do escopo (best-effort).
    if meta.get("course_id") and meta.get("lesson_number") is not None:
        await invalidate_quiz_cache_for_lesson(
            course_id=meta["course_id"],
            subject_name=meta["subject_name"],
            lesson_number=int(meta["lesson_number"]),
            material_type=meta["material_type"],
        )
    print("    ✓ vetores no Qdrant + file_sync_state=concluído")
    return True


async def process_batch(job_id: str):
    client = make_client()
    if not settings.GOOGLE_API_KEY:
        print("Erro: GOOGLE_API_KEY não configurada (necessária para descrever as imagens).")
        return

    mpath = manifest_path(job_id)
    if not mpath.exists():
        print(f"Erro: manifesto {mpath.name} não encontrado (rode o submit no mesmo diretório).")
        return
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    meta_by_id = {item["file_id"]: item for item in manifest["files"]}

    job = client.batch.jobs.get(job_id=job_id)
    print(f"Status do job {job_id}: {job.status}")
    print(f"  total={job.total_requests}  ok={job.succeeded_requests}  falhas={job.failed_requests}")

    if job.status != "SUCCESS":
        print("\nJob ainda não concluído (QUEUED/RUNNING) ou falhou. Tente mais tarde.")
        if job.error_file:
            _dump_error_file(client, job.error_file)
        return

    if job.error_file:
        _dump_error_file(client, job.error_file)

    print("\nBaixando resultados do OCR...")
    resp = client.files.download(file_id=job.output_file)
    result_path = Path(f"mistral_bulk_result_{job_id}.jsonl")
    result_path.write_bytes(resp.content)

    # Reusa SÓ o enriquecimento do parser (o OCR já foi feito pelo Batch).
    parser = MistralOCRParser()

    # PDFs grandes saem em N linhas (uma por parte) e o Batch não garante a ordem:
    # agrupamos por file_id e só ingerimos quando TODAS as partes chegaram (e então
    # liberamos a memória do arquivo — os de 1 parte finalizam na hora).
    pages_buf: dict[str, dict[int, list[dict]]] = {}  # file_id -> {part_idx: pages}
    seen: dict[str, int] = {}                          # nº de partes lidas por file_id
    bad: set[str] = set()                              # file_id com parte falha/sem páginas
    done: set[str] = set()                             # file_id já contabilizado
    ok = 0
    failed = 0

    with open(result_path, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            file_id, part_idx, n_parts = parse_custom_id(row.get("custom_id", ""))
            meta = meta_by_id.get(file_id)
            if not meta:
                print(f"  ! custom_id={row.get('custom_id')} fora do manifesto — pulando")
                continue

            seen[file_id] = seen.get(file_id, 0) + 1
            name = meta["file_name"]

            if row.get("error"):  # a chave 'error' vem sempre; só conta se truthy
                print(f"  ✗ OCR falhou (parte {part_idx + 1}/{n_parts}): {name}: {row['error']}")
                bad.add(file_id)
            else:
                pages = row.get("response", {}).get("body", {}).get("pages", [])
                if not pages:
                    print(f"  ✗ sem páginas (parte {part_idx + 1}/{n_parts}): {name}")
                    bad.add(file_id)
                else:
                    pages_buf.setdefault(file_id, {})[part_idx] = pages

            # Todas as partes deste arquivo já chegaram? finaliza e libera a memória.
            if seen[file_id] >= n_parts:
                done.add(file_id)
                parts = pages_buf.pop(file_id, {})
                if file_id in bad or len(parts) != n_parts:
                    print(f"  ⊘ {name}: incompleto/erro no OCR — não ingerido")
                    failed += 1
                elif await _ingest_file(parser, meta, [parts[i] for i in range(n_parts)]):
                    ok += 1
                else:
                    failed += 1

    # Arquivos do manifesto que nunca completaram (linha sumiu do output do Batch).
    for file_id, meta in meta_by_id.items():
        if file_id in done:
            continue
        print(f"  ⊘ {meta['file_name']}: faltaram partes no resultado do Batch — não ingerido")
        pages_buf.pop(file_id, None)
        failed += 1

    print(f"\n🎉 Bulk finalizado: {ok} ingeridos, {failed} com erro.")


# ───────────────────────────────── CLI ─────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Carga inicial em lote via Mistral Batch OCR (−50%).")
    sub = parser.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("submit", help="Lista os materiais do Drive e dispara o Batch Job de OCR.")
    sp.add_argument("--folder_id", default=None, help="Raiz no Drive (default: GOOGLE_DRIVE_FOLDER_ID).")

    pp = sub.add_parser("process", help="Baixa o resultado do job, enriquece e sobe os vetores.")
    pp.add_argument("job_id", help="ID do Batch Job devolvido pelo submit.")

    args = parser.parse_args()
    if args.command == "submit":
        asyncio.run(submit_batch(args.folder_id))
    elif args.command == "process":
        asyncio.run(process_batch(args.job_id))


if __name__ == "__main__":
    main()
