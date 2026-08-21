import os
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault(
    "RELAYCAT_BOT_TOKEN",
    "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("RELAYCAT_ADMIN_ID", "123456789")
os.environ.setdefault("RELAYCAT_SECRET_KEY", "unit-test-secret-key")
os.environ.setdefault("RELAYCAT_DB_URL", "sqlite+aiosqlite:///:memory:")

from app.database.core import _migrate_existing_tables, _sync_rule_presets  # noqa: E402
from app.database.models import AuditLog, Base, Rule, User, utc_now  # noqa: E402
from app.services.protection import (  # noqa: E402
    ProtectionPolicy,
    cleanup_old_audit_logs,
    fingerprint_content,
    record_interception,
    record_message_and_check_rate_limit,
)
from app.services.rule_presets import RULE_PRESETS  # noqa: E402


class ProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(User(id=8848, username="tester", is_verified=True))
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_minute_limit_and_repeated_interception_trigger_auto_ban(
        self,
    ) -> None:
        policy = ProtectionPolicy(
            rate_limit_enabled=True,
            messages_per_minute=2,
            burst_messages=20,
            repeat_limit=20,
            auto_ban_enabled=True,
            auto_ban_threshold=2,
            auto_ban_window_minutes=10,
            auto_ban_duration_hours=24,
        )
        now = datetime(2026, 8, 21, 12, 0, 0)
        with patch("app.services.protection.AsyncSessionLocal", self.sessions):
            first = await record_message_and_check_rate_limit(
                user_id=8848,
                username="tester",
                message_type="text",
                policy=policy,
                now=now,
            )
            second = await record_message_and_check_rate_limit(
                user_id=8848,
                username="tester",
                message_type="text",
                policy=policy,
                now=now,
            )
            limited = await record_message_and_check_rate_limit(
                user_id=8848,
                username="tester",
                message_type="text",
                policy=policy,
                now=now,
            )
            banned = await record_interception(
                event_type="rule_blocked",
                user_id=8848,
                username="tester",
                message_type="text",
                reason="测试规则",
                policy=policy,
                now=now,
            )

        self.assertFalse(first.blocked)
        self.assertFalse(second.blocked)
        self.assertTrue(limited.blocked)
        self.assertEqual(limited.reason, "超过每分钟发送上限")
        self.assertTrue(banned.auto_banned)
        async with self.sessions() as session:
            user = await session.get(User, 8848)
        self.assertTrue(user.is_banned)
        self.assertIsNotNone(user.banned_until)

    async def test_repeat_detection_uses_fingerprint_without_plaintext(self) -> None:
        fingerprint = fingerprint_content("same private message")
        self.assertIsNotNone(fingerprint)
        self.assertNotIn("private", fingerprint or "")
        self.assertEqual(
            fingerprint,
            fingerprint_content("s a\u200bme · private-message"),
        )
        policy = ProtectionPolicy(
            messages_per_minute=50,
            burst_messages=50,
            repeat_limit=2,
            auto_ban_enabled=False,
        )
        now = datetime(2026, 8, 21, 12, 0, 0)
        with patch("app.services.protection.AsyncSessionLocal", self.sessions):
            results = []
            for offset in range(3):
                results.append(
                    await record_message_and_check_rate_limit(
                        user_id=8848,
                        username="tester",
                        message_type="text",
                        content_fingerprint=fingerprint,
                        policy=policy,
                        now=now + timedelta(seconds=offset),
                    )
                )
        self.assertFalse(results[0].blocked)
        self.assertFalse(results[1].blocked)
        self.assertTrue(results[2].blocked)
        self.assertEqual(results[2].reason, "短时间重复发送相同内容")

    async def test_log_cleanup_respects_retention(self) -> None:
        now = utc_now()
        async with self.sessions() as session:
            session.add_all(
                [
                    AuditLog(
                        event_type="message_received",
                        outcome="received",
                        created_at=now - timedelta(days=31),
                    ),
                    AuditLog(
                        event_type="message_received",
                        outcome="received",
                        created_at=now,
                    ),
                ]
            )
            await session.commit()
        with patch("app.services.protection.AsyncSessionLocal", self.sessions):
            removed = await cleanup_old_audit_logs(retention_days=30)
        async with self.sessions() as session:
            remaining = await session.scalar(select(func.count(AuditLog.id)))
        self.assertEqual(removed, 1)
        self.assertEqual(remaining, 1)


class PresetSyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_adds_default_presets_and_preserves_customized_rule(self) -> None:
        protected_name = RULE_PRESETS["scam_solicitation"].name
        async with self.sessions() as session:
            session.add(
                Rule(
                    name=protected_name,
                    rule_type="message_content",
                    match_mode="contains_any",
                    pattern="我的自定义内容",
                    action="block",
                )
            )
            await session.commit()
            changed = await _sync_rule_presets(session)
            await session.commit()
            rules = (await session.execute(select(Rule))).scalars().all()

        expected_defaults = sum(
            preset.enabled_by_default
            for preset in RULE_PRESETS.values()
            if preset.name != protected_name
        )
        self.assertTrue(changed)
        self.assertEqual(len(rules), expected_defaults + 1)
        custom = next(rule for rule in rules if rule.name == protected_name)
        self.assertEqual(custom.pattern, "我的自定义内容")
        self.assertIsNone(custom.preset_id)


class DatabaseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_current_columns_to_legacy_tables(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "CREATE TABLE users (id BIGINT PRIMARY KEY, is_banned BOOLEAN)"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE rules (id INTEGER PRIMARY KEY, pattern TEXT NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, "
                        "event_type VARCHAR(40), outcome VARCHAR(32), "
                        "user_id BIGINT, created_at TIMESTAMP)"
                    )
                )
                await connection.run_sync(_migrate_existing_tables)

                def schema(sync_connection):
                    inspector = inspect(sync_connection)
                    return (
                        {
                            table: {
                                item["name"] for item in inspector.get_columns(table)
                            }
                            for table in ("users", "rules", "audit_logs")
                        },
                        {item["name"] for item in inspector.get_indexes("audit_logs")},
                    )

                migrated, indexes = await connection.run_sync(schema)
        finally:
            await engine.dispose()

        self.assertTrue({"banned_until", "ban_reason"} <= migrated["users"])
        self.assertTrue(
            {"match_mode", "name", "preset_id", "preset_version"} <= migrated["rules"]
        )
        self.assertIn("content_fingerprint", migrated["audit_logs"])
        self.assertIn("ix_audit_log_user_fingerprint_time", indexes)


if __name__ == "__main__":
    unittest.main()
