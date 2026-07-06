import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.database.models import Base, Rule

logger = logging.getLogger(__name__)

if settings.database_url.startswith("sqlite"):
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Rule.id).limit(1))
        if result.scalar_one_or_none() is None:
            session.add_all(
                [
                    Rule(
                        rule_type="message_content",
                        pattern=r"(兼职|刷单|日结|加V|VX|微信|卖茶|投资|理财|USDT|BTC)",
                        action="block",
                    ),
                    Rule(
                        rule_type="message_content",
                        pattern=r"(http|https)://(t\.me|telegram\.me)/",
                        action="block",
                    ),
                    Rule(
                        rule_type="username",
                        pattern=r"(bot|admin|support|service)",
                        action="block",
                    ),
                ]
            )
            await session.commit()
            logger.info("Default filtering rules created")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
