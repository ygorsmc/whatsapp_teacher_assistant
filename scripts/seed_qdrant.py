import asyncio
import uuid
from sqlalchemy import text
from src.db.database import AsyncSessionLocal
from src.rag.qdrant_client import init_qdrant_collection, get_qdrant
from src.llm.factory import get_embedder
from qdrant_client.models import PointStruct
import src.llm # triggers providers init

async def seed_qdrant():
    print("Inicializando Coleção Qdrant...")
    await init_qdrant_collection("documents")
    qdrant = await get_qdrant()
    embedder = get_embedder("gemini")

    print("Buscando Professor e Curso no banco SQLite...")
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id, name FROM Teachers WHERE name = 'Professor Teste' LIMIT 1"))
        teacher = result.fetchone()
        if not teacher:
            print("❌ Professor não encontrado! Rode o seed_local_db.py primeiro.")
            return
        teacher_id = teacher[0]

        result = await session.execute(text("SELECT id, name FROM Courses WHERE teacher_id = :tid LIMIT 1"), {"tid": teacher_id})
        course = result.fetchone()
        course_id = course[0]

    print(f"Professor ID: {teacher_id}")
    print(f"Course ID: {course_id}")

    # Conteúdos sobre o Teorema de Pitágoras para a Aula 1
    chunks = [
        "O Teorema de Pitágoras diz que em qualquer triângulo retângulo, o quadrado do comprimento da hipotenusa é igual à soma dos quadrados dos comprimentos dos catetos (a² + b² = c²).",
        "A hipotenusa é sempre o lado mais longo do triângulo retângulo e fica oposto ao ângulo reto de 90 graus.",
        "O Teorema de Pitágoras só se aplica a triângulos retângulos. Ele não funciona para triângulos acutângulos ou obtusângulos."
    ]

    print("Gerando Embeddings (Gemini)...")
    embeddings = await embedder.aembed_documents(chunks)

    print("Inserindo no Qdrant...")
    points = []
    for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "teacher_id": teacher_id,
                    "course_id": course_id,
                    "lesson_id": "lesson_1",
                    "content": chunk,
                    "source": "Apostila Matemática Básica - Aula 1"
                }
            )
        )
        
    await qdrant.upsert(
        collection_name="documents",
        points=points
    )
    
    print("✅ Seed do Qdrant concluído com sucesso!")

if __name__ == "__main__":
    asyncio.run(seed_qdrant())
