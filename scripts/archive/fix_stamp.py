from alembic.config import Config
from alembic import command
import os

os.environ['DATABASE_URL'] = "postgresql+asyncpg://pgadmin:phrlN63zr5vZMCobPnVwkw==@whatsapp-bot-pg-bdn4p6wsfyc3i.postgres.database.azure.com:5432/whatsapp_assistant?ssl=require"

alembic_cfg = Config("alembic.ini")
command.stamp(alembic_cfg, "head")
print("stamped")
