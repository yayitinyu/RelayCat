import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.database.models import Base

# Configuration
DATA_DIR = os.getenv("RELAYCAT_DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_URL = os.getenv("RELAYCAT_DB_URL", f"sqlite+aiosqlite:///{DATA_DIR}/relaycat.db")

engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
