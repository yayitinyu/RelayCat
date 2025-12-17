import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.future import select

from app.settings import settings
from app.database.models import Base, Rule

# Configuration
DATA_DIR = os.getenv("RELAYCAT_DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_URL = os.getenv("RELAYCAT_DB_URL", f"sqlite+aiosqlite:///{DATA_DIR}/relaycat.db")

engine = create_async_engine(DB_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Seed Rules
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Rule).limit(1))
        if not result.scalar_one_or_none():
            default_rules = [
                Rule(rule_type="message_content", pattern=r"(兼职|刷单|日结|加V|VX|微信|卖茶|投资|理财|USDT|BTC)", action="block"),
                Rule(rule_type="message_content", pattern=r"(http|https)://(t\.me|telegram\.me)/", action="block"),
                Rule(rule_type="username", pattern=r"(bot|admin|support|service)", action="block"),
            ]
            session.add_all(default_rules)
            await session.commit()
            print("✅ Default rules seeded.")

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
