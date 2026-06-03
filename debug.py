import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        # Check Students
        res = await session.execute(text("SELECT name, phone, is_active FROM Students"))
        students = res.fetchall()
        print("=== STUDENTS ===")
        for s in students:
            print(f"{s.name} - {s.phone} - active:{s.is_active}")
            
        # Check Schedules
        res = await session.execute(text("SELECT s.name, ss.preferred_hour, ss.status, ss.next_trigger_date FROM StudentSchedule ss JOIN Students s ON s.id = ss.student_id"))
        schedules = res.fetchall()
        print("\n=== SCHEDULES ===")
        for s in schedules:
            print(f"{s[0]} - {s[1]} - {s[2]} - {s[3]}")

if __name__ == "__main__":
    asyncio.run(main())
