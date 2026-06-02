import sys

with open("src/drive/spreadsheet_sync.py", "r") as f:
    content = f.read()

# 1. Fix Teachers
old_teacher = """                if use_mssql:
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
                    teacher_id = res.scalar()"""

new_teacher = """                if use_mssql:
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
                    res = await session.execute(text("SELECT id FROM Teachers WHERE phone = :phone"), {"phone": phone})
                    teacher_id = res.scalar()"""
content = content.replace(old_teacher, new_teacher)

# 2. Fix Students
old_student = """                if use_mssql:
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
                    student_id = res.scalar()"""

new_student = """                if use_mssql:
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
                    res = await session.execute(text("SELECT id FROM Students WHERE phone = :phone"), {"phone": phone})
                    student_id = res.scalar()"""
content = content.replace(old_student, new_student)

# 3. Remove duplicate Courses
duplicate_courses = """                # 2.5 Upsert Class

                if use_mssql:
                    await session.execute(
                        text(\"\"\"
                            MERGE INTO Courses AS target
                            USING (SELECT :id AS id) AS source
                            ON target.id = source.id
                            WHEN NOT MATCHED THEN INSERT (id, teacher_id, name, is_active)
                                VALUES (:id, :tid, :name, 1);
                        \"\"\"),
                        {"id": course_id, "tid": default_teacher_id, "name": course_name}
                    )
                else:
                    await session.execute(
                        text(\"\"\"
                            INSERT OR IGNORE INTO Courses (id, teacher_id, name, is_active)
                            VALUES (:id, :tid, :name, 1)
                        \"\"\"),
                        {"id": course_id, "tid": default_teacher_id, "name": course_name}
                    )

                class_id = generate_id("cls", f"{course_id}_{class_name}")"""

fixed_courses = """                # 2.5 Upsert Class
                class_id = generate_id("cls", f"{course_id}_{class_name}")"""

content = content.replace(duplicate_courses, fixed_courses)

with open("src/drive/spreadsheet_sync.py", "w") as f:
    f.write(content)

print("Fixes applied.")
