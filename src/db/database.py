from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy import text
import structlog
from src.shared.config import settings

logger = structlog.get_logger(__name__)

# ── Professor admin de bootstrap ───────────────────────────────────────────
# Precisa SEMPRE existir: é o professor usado nas primeiras configurações
# (ex.: rodar /sinc_alunos a primeira vez, antes de existirem professores
# reais no banco). Número fictício 11000000000 → 5511000000000 após normalização.
# NÃO deve ser removido. Mantém telegram_chat_id NULL para não "roubar" a
# identidade de um professor real (vincular o chat de alguém a ele faz /quiz e
# /aula resolverem o professor errado).
BOOTSTRAP_ADMIN_ID = "00000000-0000-0000-0000-000000000001"
BOOTSTRAP_ADMIN_PHONE = "5511000000000"
BOOTSTRAP_ADMIN_NAME = "Admin"

# Used for local dev (SQLite) or production (PostgreSQL with asyncpg)
# Production URL would look like: postgresql+asyncpg://<user>:<pwd>@<server>/<db>
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def ensure_bootstrap_admin():
    """Garante (idempotente, não-destrutivo) que o professor admin de bootstrap exista.

    Seguro para rodar em produção: usa INSERT ... ON CONFLICT DO NOTHING, então
    nunca sobrescreve um admin já existente (preserva telegram_chat_id/is_active
    que tenham sido definidos de propósito) nem apaga nada.
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    'INSERT INTO "Teachers" '
                    '(id, name, subject, phone, assistant_persona, out_of_scope_message, is_active) '
                    "VALUES (:id, :name, 'Geral', :phone, 'Seja educado e prestativo.', 'Isso está fora do escopo.', true) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": BOOTSTRAP_ADMIN_ID, "name": BOOTSTRAP_ADMIN_NAME, "phone": BOOTSTRAP_ADMIN_PHONE},
            )
            await session.commit()
    except Exception as e:
        # Bootstrap nunca deve derrubar o startup do app.
        logger.warning("ensure_bootstrap_admin_failed", error=str(e))


async def init_db():
    """Create all tables. Useful for local dev with SQLite or initial Azure SQL setup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_bootstrap_admin()
