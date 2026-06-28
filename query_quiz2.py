import asyncio
import json
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT topic, question_text, alternatives, correct_answer, explanation, summary, key_points
                FROM "QuizBank"
                WHERE lesson_number = 2
                ORDER BY created_at ASC
            """)
        )
        rows = result.fetchall()
        
        with open("lesson2_quizzes.md", "w", encoding="utf-8") as f:
            f.write("# Revisões e Quizzes da Aula 2\n\n")
            f.write(f"Total de questões salvas: {len(rows)}\n\n")
            
            topics = []
            seen = set()
            for r in rows:
                if r.topic not in seen:
                    topics.append(r.topic)
                    seen.add(r.topic)
            
            f.write(f"## Tópicos Extraídos ({len(topics)})\n")
            for t in topics:
                f.write(f"- {t}\n")
            f.write("\n---\n\n")
            
            for idx, r in enumerate(rows, 1):
                f.write(f"### Questão {idx}\n")
                f.write(f"**Tópico:** {r.topic}\n\n")
                f.write(f"**Revisão (Summary):**\n{r.summary}\n\n")
                f.write(f"**Pergunta:**\n{r.question_text}\n\n")
                alts = json.loads(r.alternatives) if isinstance(r.alternatives, str) else r.alternatives
                for a in alts:
                    f.write(f"- **{a['id']}**: {a['text']}\n")
                f.write(f"\n**Resposta Correta:** {r.correct_answer}\n")
                f.write(f"**Explicação:** {r.explanation}\n")
                f.write("\n---\n\n")
        
        print("Arquivo lesson2_quizzes.md gerado com sucesso!")

asyncio.run(main())
