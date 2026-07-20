import logging
from pathlib import Path

from sqlalchemy import inspect, select, text
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
        await connection.run_sync(_migrate_existing_tables)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Rule.id).limit(1))
        if result.scalar_one_or_none() is None:
            session.add_all(
                [
                    Rule(
                        name="高风险诈骗招揽",
                        rule_type="message_content",
                        match_mode="contains_any",
                        pattern="刷单\n跑分\n稳赚不赔\n博彩平台\n裸聊\n代充返利\nUSDT 搬砖\n带单老师",
                        action="block",
                    ),
                    Rule(
                        name="高风险邀请与短链接",
                        rule_type="message_content",
                        match_mode="regex",
                        pattern=r"(?:https?://)?(?:t\.me|telegram\.me)/(?:joinchat/|\+)|(?:https?://)?(?:bit\.ly|tinyurl\.com)/",
                        action="block",
                    ),
                    Rule(
                        name="常见导流联系方式",
                        rule_type="message_content",
                        match_mode="contains_any",
                        pattern="加微信\n加V详聊\n私聊返利\n联系客服领\n进群领取",
                        action="block",
                    ),
                ]
            )
            await session.commit()
            logger.info("Default filtering rules created")


def _migrate_existing_tables(sync_connection) -> None:
    """Add columns introduced after the first release without a migration service."""
    inspector = inspect(sync_connection)
    migrations = {
        "users": {
            "banned_until": "TIMESTAMP NULL",
            "ban_reason": "VARCHAR(255) NULL",
        },
        "rules": {
            "match_mode": "VARCHAR(32) NOT NULL DEFAULT 'regex'",
            "name": "VARCHAR(120) NULL",
        },
    }
    for table_name, columns in migrations.items():
        if table_name not in inspector.get_table_names():
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, definition in columns.items():
            if column_name not in existing:
                sync_connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
                )


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
