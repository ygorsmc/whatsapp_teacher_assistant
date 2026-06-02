import sys

with open("src/drive/spreadsheet_sync.py", "r") as f:
    content = f.read()

# Fix Teachers
old_teacher_mssql = """                            MERGE INTO Teachers AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                                VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active);"""

new_teacher_mssql = """                            MERGE INTO Teachers AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                                VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active)
                            OUTPUT inserted.id;"""

old_teacher_sqlite = """                            INSERT INTO Teachers (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                            VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP"""

new_teacher_sqlite = """                            INSERT INTO Teachers (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                            VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP
                            RETURNING id"""

# Replace in file and capture the result in the execution code
# Find the teacher execution block
teacher_execute_mssql = """                    await session.execute("""
teacher_execute_mssql_replace = """                    res = await session.execute("""

# Let's just do a string replace for the whole teacher logic
teacher_logic_old = """                if use_mssql:
                    await session.execute(
                        text(\"\"\"
                            MERGE INTO Teachers AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                                VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active);
                        \"\"\"),
                        {"id": teacher_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                else:
                    await session.execute(
                        text(\"\"\"
                            INSERT INTO Teachers (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                            VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP
                        \"\"\"),
                        {"id": teacher_id, "name": name, "phone": phone, "is_active": is_active}
                    )"""

teacher_logic_new = """                if use_mssql:
                    res = await session.execute(
                        text(\"\"\"
                            MERGE INTO Teachers AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                                VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active)
                            OUTPUT inserted.id;
                        \"\"\"),
                        {"id": teacher_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    teacher_id = res.scalar()
                else:
                    res = await session.execute(
                        text(\"\"\"
                            INSERT INTO Teachers (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                            VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP
                            RETURNING id
                        \"\"\"),
                        {"id": teacher_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    teacher_id = res.scalar()"""

content = content.replace(teacher_logic_old, teacher_logic_new)


# Now fix Students
student_logic_old = """                if use_mssql:
                    await session.execute(
                        text(\"\"\"
                            MERGE INTO Students AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, phone, is_active)
                                VALUES (:id, :name, :phone, :is_active);
                        \"\"\"),
                        {"id": student_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                else:
                    await session.execute(
                        text(\"\"\"
                            INSERT INTO Students (id, name, phone, is_active)
                            VALUES (:id, :name, :phone, :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP
                        \"\"\"),
                        {"id": student_id, "name": name, "phone": phone, "is_active": is_active}
                    )"""

student_logic_new = """                if use_mssql:
                    res = await session.execute(
                        text(\"\"\"
                            MERGE INTO Students AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, phone, is_active)
                                VALUES (:id, :name, :phone, :is_active)
                            OUTPUT inserted.id;
                        \"\"\"),
                        {"id": student_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    student_id = res.scalar()
                else:
                    res = await session.execute(
                        text(\"\"\"
                            INSERT INTO Students (id, name, phone, is_active)
                            VALUES (:id, :name, :phone, :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP
                            RETURNING id
                        \"\"\"),
                        {"id": student_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    student_id = res.scalar()"""

content = content.replace(student_logic_old, student_logic_new)

with open("src/drive/spreadsheet_sync.py", "w") as f:
    f.write(content)

print("Patch applied")
