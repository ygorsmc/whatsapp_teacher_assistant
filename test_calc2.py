from datetime import datetime, timedelta, timezone, date

# Simula o cálculo do sync se o professor rodou /sinc_alunos ANTES das 23:00
schedule_map = {"2": "15:00:00", "5": "23:00", "6": "15:00:00"}
start_date_obj = date(2026, 6, 20)

# Simula rodar o /sinc_alunos às 22:55 (antes do horário da aula)
brt = timezone(timedelta(hours=-3))
simulated_now = datetime(2026, 6, 20, 22, 55, 0, tzinfo=brt)
print(f"Simulated sync at: {simulated_now}")

schedules = []
for wd_str, time_str in schedule_map.items():
    try:
        t = datetime.strptime(time_str[:5], "%H:%M").time()
        schedules.append((int(wd_str), t))
    except ValueError:
        pass
print(f"Parsed schedules: {schedules}")

elapsed = 0
curr_date = start_date_obj
target_date = simulated_now.date()
target_time = simulated_now.time()

while curr_date <= target_date:
    wd = curr_date.weekday()
    for sched_wd, sched_time in schedules:
        if wd == sched_wd:
            if curr_date < target_date or (curr_date == target_date and target_time >= sched_time):
                elapsed += 1
                print(f"  +1: date={curr_date}, weekday={wd}, sched_time={sched_time}")
            else:
                print(f"  SKIP: date={curr_date}, weekday={wd}, sched_time={sched_time} (target_time {target_time} < sched_time)")
    curr_date += timedelta(days=1)

print(f"\nTotal elapsed: {elapsed}")
print(f"Se a turma estava em current_lesson=1 no banco:")
print(f"  excluded.current_lesson_number ({elapsed}) < Classes.current_lesson_number (1)? {elapsed < 1}")
print(f"  => O ON CONFLICT RESETA para {elapsed}!")
