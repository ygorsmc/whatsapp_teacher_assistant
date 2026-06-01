import asyncio
from src.rag.retriever import retrieve_context
from src.shared.config import settings

async def debug():
    try:
        print("Buscando RAG...")
        context = await retrieve_context(
            teacher_id="test",
            course_id="test",
            allowed_lesson_ids=["001"],
            query="Olá"
        )
        print("Success RAG!")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(debug())
