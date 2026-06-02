import sys

with open("src/drive/spreadsheet_sync.py", "r") as f:
    content = f.read()

pass_1_5_start = content.find("        # Pass 1.5: Sync Calendar (Turmas Tab)")
pass_2_start = content.find("        # Pass 2: Upsert Students, Courses, Enrollments, and Schedules")
pass_3_start = content.find("        # Pass 3: Cleanup orphaned enrollments and schedules for the synced students")

if pass_1_5_start != -1 and pass_2_start != -1 and pass_3_start != -1:
    pass_1_5_code = content[pass_1_5_start:pass_2_start]
    
    new_content = content[:pass_1_5_start] + content[pass_2_start:pass_3_start] + pass_1_5_code + content[pass_3_start:]
    
    with open("src/drive/spreadsheet_sync.py", "w") as f:
        f.write(new_content)
    print("Pass 1.5 moved successfully!")
else:
    print("Failed to find pass markers.")
