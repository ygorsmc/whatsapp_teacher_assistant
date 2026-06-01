from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from src.shared.config import settings

# Used for local dev (SQLite) or production (Azure SQL with asyncpg/aioodbc)
# Production URL would look like: mssql+aioodbc://<client_id>@<server>.database.windows.net/<db>?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryMsi
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
    """Create all tables. Useful for local dev with SQLite."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
