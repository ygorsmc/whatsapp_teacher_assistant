import sys

with open("src/drive/spreadsheet_sync.py", "r") as f:
    content = f.read()

target = """                if not default_teacher_id:
                    default_teacher_id = teacher_id"""

# I need to move the default_teacher_id assignment to AFTER the MERGE.
# Or just replace it at the end of the if role == "professor" block.
# Actually, I can just find where `teacher_id = res.scalar()` is.

content = content.replace("teacher_id = res.scalar()", "teacher_id = res.scalar()\n                    if default_teacher_id == generate_id('tea', phone):\n                        default_teacher_id = teacher_id")

with open("src/drive/spreadsheet_sync.py", "w") as f:
    f.write(content)

print("default_teacher_id patched")
