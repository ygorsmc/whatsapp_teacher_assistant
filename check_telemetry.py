import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal
from datetime import timezone

async def query_interactions():
    print("Conectando ao banco de dados e buscando as interações recentes...\n")
    async with AsyncSessionLocal() as session:
        # Busca as últimas interações
        result = await session.execute(
            text("""
                SELECT 
                    s.name as Aluno,
                    si.interaction_type as Evento,
                    si.timestamp as Horario,
                    si.is_correct as Acertou,
                    si.message_content as Mensagem
                FROM StudentInteractions si
                JOIN Students s ON s.id = si.student_id
                ORDER BY si.timestamp DESC
                LIMIT 10
            """)
        )
        
        rows = result.fetchall()
        if not rows:
            print("Nenhuma interação encontrada ainda! Mande uma mensagem para o bot.")
            return

        print(f"{'Aluno':<15} | {'Evento':<18} | {'Horário (UTC)':<20} | {'Acertou?':<10} | {'Mensagem'}")
        print("-" * 100)
        for row in rows:
            aluno = row.Aluno[:15]
            evento = row.Evento
            horario = row.Horario.strftime('%Y-%m-%d %H:%M:%S') if row.Horario else "N/A"
            acertou = str(row.Acertou) if row.Acertou is not None else "N/A"
            msg = row.Mensagem[:30] + "..." if row.Mensagem and len(row.Mensagem) > 30 else (row.Mensagem or "N/A")
            
            print(f"{aluno:<15} | {evento:<18} | {horario:<20} | {acertou:<10} | {msg}")
            
        print("\n--- TESTE DA JANELA DE 24 HORAS ---")
        
        # Testando a query da janela do primeiro aluno encontrado
        first_student_name = rows[0].Aluno
        res_id = await session.execute(text("SELECT id FROM Students WHERE name = :name"), {"name": first_student_name})
        student_id = res_id.scalar()
        
        res_window = await session.execute(
            text("""
                SELECT MAX(timestamp) 
                FROM StudentInteractions 
                WHERE student_id = :sid 
                  AND interaction_type IN ('message_received', 'quiz_answered')
            """),
            {"sid": student_id}
        )
        last_interaction = res_window.scalar()
        print(f"Aluno: {first_student_name}")
        print(f"Última Interação (Inbound): {last_interaction}")
        
        import datetime
        now = datetime.datetime.now(timezone.utc).replace(tzinfo=None) # Ajuste para sqlite nativo
        if last_interaction:
            diff = now - last_interaction
            hours = diff.total_seconds() / 3600
            print(f"Horas desde a última interação: {hours:.2f}h")
            if hours <= 24:
                print("✅ STATUS DA JANELA: ABERTA!")
            else:
                print("❌ STATUS DA JANELA: FECHADA!")
        else:
            print("❌ STATUS DA JANELA: FECHADA (Nenhuma interação inbound encontrada)")


if __name__ == "__main__":
    asyncio.run(query_interactions())
