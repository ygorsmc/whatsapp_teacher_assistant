from datetime import datetime, timedelta, timezone

# O que o scheduler faz
brt = timezone(timedelta(hours=-3))
agora = datetime.now(brt)
hoje_str = agora.date().isoformat()
print(f"agora (BRT): {agora}")
print(f"hoje_str: {hoje_str}")

# O que está no banco (last_advanced_date é UTC sem timezone)
last_adv_str = "2026-06-21 01:45:23.976128"  # valor real do banco
print(f"last_adv_str[:10]: {last_adv_str[:10]}")
print(f"Match? {last_adv_str[:10] == hoje_str}")
print()
print("PROBLEMA: hoje_str = 2026-06-20 (BRT), mas last_advanced_date = 2026-06-21 (UTC)!")
print("O scheduler grava CURRENT_TIMESTAMP (UTC) mas compara com data BRT.")
print("Resultado: no dia seguinte em BRT, a guarda não funciona e ele avança de novo.")
