import re

with open("src/workers/command_worker.py", "r") as f:
    code = f.read()

# 1. Fix /aula command
old_aula = """        async with AsyncSessionLocal() as session:
            result = await session.execute(text('SELECT id FROM "Teachers" WHERE phone = :phone'), {"phone": sender_id})
            teacher = result.fetchone()
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return"""

new_aula = """        async with AsyncSessionLocal() as session:
            query_col = channel.get_user_query_column()
            result = await session.execute(text(f'SELECT id FROM "Teachers" WHERE {query_col} = :sid'), {"sid": sender_id})
            teacher = result.fetchone()
            
            if not teacher:
                res_stu = await session.execute(text(f'SELECT phone FROM "Students" WHERE {query_col} = :sid'), {"sid": sender_id})
                stu_phone = res_stu.scalar_one_or_none()
                if stu_phone:
                    phone_suffix = stu_phone[-8:] if len(stu_phone) >= 8 else stu_phone
                    res_tea = await session.execute(text('SELECT id FROM "Teachers" WHERE phone LIKE :phone_like'), {"phone_like": f"%{phone_suffix}"})
                    teacher = res_tea.fetchone()
                    if teacher:
                        await session.execute(text(f'UPDATE "Teachers" SET {query_col} = :sid WHERE id = :tid'), {"sid": sender_id, "tid": teacher[0]})
                        await session.commit()
                        
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return"""
code = code.replace(old_aula, new_aula)

# 2. Fix /quiz command
old_quiz = """            query_col = channel.get_user_query_column()
            result = await session.execute(text(f'SELECT id FROM "Teachers" WHERE {query_col} = :sid'), {"sid": sender_id})
            teacher = result.fetchone()
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return"""

new_quiz = """            query_col = channel.get_user_query_column()
            result = await session.execute(text(f'SELECT id FROM "Teachers" WHERE {query_col} = :sid'), {"sid": sender_id})
            teacher = result.fetchone()
            
            if not teacher:
                res_stu = await session.execute(text(f'SELECT phone FROM "Students" WHERE {query_col} = :sid'), {"sid": sender_id})
                stu_phone = res_stu.scalar_one_or_none()
                if stu_phone:
                    phone_suffix = stu_phone[-8:] if len(stu_phone) >= 8 else stu_phone
                    res_tea = await session.execute(text('SELECT id FROM "Teachers" WHERE phone LIKE :phone_like'), {"phone_like": f"%{phone_suffix}"})
                    teacher = res_tea.fetchone()
                    if teacher:
                        await session.execute(text(f'UPDATE "Teachers" SET {query_col} = :sid WHERE id = :tid'), {"sid": sender_id, "tid": teacher[0]})
                        await session.commit()
                        
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return"""
code = code.replace(old_quiz, new_quiz)

# 3. Fix /listar_cursos command
old_listar = """        async with AsyncSessionLocal() as session:
            query_col = channel.get_user_query_column()
            result = await session.execute(text(f'SELECT id FROM "Teachers" WHERE {query_col} = :sid'), {"sid": sender_id})
            teacher = result.fetchone()
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return"""

code = code.replace(old_listar, new_aula) # new_aula is identical to the fallback logic needed here!


# 4. Fix ILIKE class names
code = code.replace('WHERE c.name = :cname AND crs.teacher_id = :tid', "WHERE c.name ILIKE '%' || :cname || '%' AND crs.teacher_id = :tid")

with open("src/workers/command_worker.py", "w") as f:
    f.write(code)

print("Patch applied!")
