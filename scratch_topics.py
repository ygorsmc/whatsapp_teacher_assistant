import asyncio
import json
import redis.asyncio as redis
from sqlalchemy import text
from src.shared.config import settings
from src.db.database import AsyncSessionLocal

async def get_topics():
    r = redis.from_url(settings.REDIS_URL, decode_responses=True)
    keys = await r.keys("topics:*")
    if not keys:
        print("Nenhum tópico encontrado no cache.")
        return
    
    async with AsyncSessionLocal() as session:
        for key in keys:
            parts = key.split(":")
            course_id = parts[1]
            subject_name = parts[2] if len(parts) > 3 else "default"
            lesson_num = parts[3] if len(parts) > 3 else parts[2]
            
            res = await session.execute(text('SELECT name FROM "Courses" WHERE id = :cid'), {"cid": course_id})
            course_name = res.scalar() or "Curso Desconhecido"
            
            val = await r.get(key)
            topics = json.loads(val)
            print(f"[{course_name} / {subject_name.replace('_', ' ').title()}] Aula {lesson_num}:")
            for t in topics:
                print(f"  - {t}")
            print()

asyncio.run(get_topics())
