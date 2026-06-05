from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from src.shared.config import settings

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

async def init_db():
    """Create all tables. Useful for local dev with SQLite or initial Azure SQL setup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
