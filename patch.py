import sys

with open("src/drive/spreadsheet_sync.py", "r") as f:
    content = f.read()

courses_upsert = """
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

"""

# Find where class_id is generated in Pass 1.5
target = '                class_id = generate_id("cls", f"{course_id}_{class_name}")'
if target in content:
    new_content = content.replace(target, courses_upsert + target)
    with open("src/drive/spreadsheet_sync.py", "w") as f:
        f.write(new_content)
    print("Patched successfully")
else:
    print("Target not found")
