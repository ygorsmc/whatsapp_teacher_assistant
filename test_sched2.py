from datetime import datetime, timedelta, timezone, date
import json

brt = timezone(timedelta(hours=-3))
agora = datetime.now(brt)
hoje_str = agora.date().isoformat()
weekday = str(agora.weekday())

print(f"agora (BRT): {agora}")
print(f"hoje_str: {hoje_str}")
print(f"weekday: {weekday} (0=Mon..6=Sun)")

schedule_map = {"2": "15:00:00", "5": "23:00", "6": "15:00:00"}
print(f"schedule_map: {schedule_map}")
print(f"weekday '{weekday}' in smap? {weekday in schedule_map}")

if weekday in schedule_map:
    class_time_str = schedule_map[weekday]
    class_time = datetime.strptime(class_time_str[:5], "%H:%M").time()
    print(f"class_time: {class_time}")
    print(f"agora.time(): {agora.time()}")
    print(f"agora.time() >= class_time? {agora.time() >= class_time}")

# Check last_advanced_date guard
last_adv = datetime(2026, 6, 21, 1, 53, 40, 817515)  # UTC, from DB
print(f"\nlast_advanced_date (UTC): {last_adv}")
last_adv_utc = last_adv.replace(tzinfo=timezone.utc)
last_adv_brt = last_adv_utc.astimezone(brt)
print(f"last_advanced_date (BRT): {last_adv_brt}")
print(f"last_adv_brt.date().isoformat(): {last_adv_brt.date().isoformat()}")
print(f"Match hoje_str? {last_adv_brt.date().isoformat() == hoje_str}")
print(f"=> Scheduler would SKIP this class (already advanced today)? {last_adv_brt.date().isoformat() == hoje_str}")

# current_lesson = 0
current_lesson = 0
new_lesson = current_lesson + 1 if current_lesson >= 1 else 1
print(f"\ncurrent_lesson: {current_lesson} -> new_lesson: {new_lesson}")
