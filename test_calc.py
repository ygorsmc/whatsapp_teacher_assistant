from datetime import datetime, date, timedelta, timezone

# Simula exatamente o que o sync faz
schedule_map = {"2": "15:00:00", "5": "22:45", "6": "15:00:00"}
start_date_obj = date(2026, 6, 20)  # hoje

TIMEZONE_OFFSET_HOURS = -3
local_tz = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
now_local = datetime.now(local_tz)
print(f"now_local: {now_local}")
print(f"now_local.date(): {now_local.date()}")
print(f"now_local.weekday(): {now_local.weekday()}")
print(f"start_date_obj: {start_date_obj}")

schedules = []
for wd_str, time_str in schedule_map.items():
    try:
        t = datetime.strptime(time_str[:5], "%H:%M").time()
        schedules.append((int(wd_str), t))
    except ValueError:
        pass

print(f"Parsed schedules (weekday, time): {schedules}")

elapsed = 0
curr_date = start_date_obj
target_date = now_local.date()
target_time = now_local.time()

while curr_date <= target_date:
    wd = curr_date.weekday()
    for sched_wd, sched_time in schedules:
        if wd == sched_wd:
            if curr_date < target_date or (curr_date == target_date and target_time >= sched_time):
                elapsed += 1
                print(f"  +1: date={curr_date}, weekday={wd}, sched_wd={sched_wd}, sched_time={sched_time}")
    curr_date += timedelta(days=1)

print(f"\nTotal elapsed: {elapsed}")
print(f"current_lesson would be: {elapsed}")
