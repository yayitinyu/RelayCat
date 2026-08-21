import logging
from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.database.models import Base, Rule
from app.services.rule_presets import RULE_PRESETS, RulePreset

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
        changed = await _sync_rule_presets(session)
        if changed:
            await session.commit()
            logger.info("Filtering presets synchronized")


async def _sync_rule_presets(session: AsyncSession) -> bool:
    preset_ids = set(RULE_PRESETS)
    result = await session.execute(select(Rule).where(Rule.preset_id.in_(preset_ids)))
    rules_by_preset = {rule.preset_id: rule for rule in result.scalars()}
    changed = False

    for preset in RULE_PRESETS.values():
        rule = rules_by_preset.get(preset.preset_id)
        if rule is None:
            rule = await session.scalar(select(Rule).where(Rule.name == preset.name))
            if rule is not None and not _is_legacy_preset(rule, preset):
                continue
            if rule is None and not preset.enabled_by_default:
                continue
            if rule is None:
                rule = Rule()
                session.add(rule)

        if (rule.preset_version or 0) >= preset.version:
            continue
        rule.preset_id = preset.preset_id
        rule.preset_version = preset.version
        rule.name = preset.name
        rule.rule_type = preset.rule_type
        rule.match_mode = preset.match_mode
        rule.pattern = preset.pattern
        rule.action = preset.action
        changed = True

    return changed


def _is_legacy_preset(rule: Rule, preset: RulePreset) -> bool:
    return rule.pattern == preset.pattern or rule.pattern in preset.legacy_patterns


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
            "preset_id": "VARCHAR(64) NULL",
            "preset_version": "INTEGER NULL",
        },
        "audit_logs": {
            "content_fingerprint": "VARCHAR(64) NULL",
        },
    }
    for table_name, columns in migrations.items():
        if table_name not in inspector.get_table_names():
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, definition in columns.items():
            if column_name not in existing:
                sync_connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
                    )
                )
    current_inspector = inspect(sync_connection)
    audit_columns = (
        {column["name"] for column in current_inspector.get_columns("audit_logs")}
        if "audit_logs" in current_inspector.get_table_names()
        else set()
    )
    if {"user_id", "content_fingerprint", "created_at"} <= audit_columns:
        sync_connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_audit_log_user_fingerprint_time "
                "ON audit_logs (user_id, content_fingerprint, created_at)"
            )
        )


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
