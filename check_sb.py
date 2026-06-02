from src.shared.config import settings
print("FQDN:", repr(settings.SERVICEBUS_FQDN))
print("CS_SET:", bool(settings.SERVICEBUS_CONNECTION_STRING))
cs = settings.SERVICEBUS_CONNECTION_STRING or ""
print("CS_PREFIX:", cs[:80])
print("HAS_EMULATOR:", "UseDevelopmentEmulator" in cs)
