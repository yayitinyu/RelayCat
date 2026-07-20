import os
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault(
    "RELAYCAT_BOT_TOKEN",
    "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("RELAYCAT_ADMIN_ID", "123456789")
os.environ.setdefault("RELAYCAT_DB_URL", "sqlite+aiosqlite:///:memory:")

from app.core.config import Settings  # noqa: E402
from app.core.secret_store import decrypt_secret, encrypt_secret  # noqa: E402
from app.database.core import _migrate_existing_tables  # noqa: E402
from app.database.models import AuditLog, Base, Rule, User, utc_now  # noqa: E402
from app.services.ai import (  # noqa: E402
    AIReplyClient,
    AIResponseError,
    parse_review_decision,
)
from app.services.filtering import (  # noqa: E402
    message_has_link,
    is_bot_command,
    rule_matches,
)
from app.services.protection import (  # noqa: E402
    ProtectionPolicy,
    cleanup_old_audit_logs,
    record_interception,
    record_message_and_check_rate_limit,
)
from app.web.routes import normalize_ai_base_url, validate_rule  # noqa: E402


def make_settings(**overrides) -> Settings:
    values = {
        "bot_token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "admin_id": 123456789,
        "ai_api_key": SecretStr("test-key"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class SettingsTests(unittest.TestCase):
    def test_accepts_custom_port(self) -> None:
        self.assertEqual(make_settings(port=9180).port, 9180)

    def test_rejects_invalid_port(self) -> None:
        with self.assertRaises(ValidationError):
            make_settings(port=70000)


class RuleValidationTests(unittest.TestCase):
    def test_rejects_invalid_regex(self) -> None:
        self.assertEqual(
            validate_rule("message_content", "([", "block"),
            "正则表达式格式无效",
        )

    def test_accepts_supported_rule(self) -> None:
        self.assertIsNone(validate_rule("username", r"(spam|ad)", "drop"))

    def test_plain_keyword_mode_does_not_require_regex(self) -> None:
        self.assertIsNone(
            validate_rule("message_content", "([\n广告", "block", "contains_any")
        )

    def test_matches_unicode_keywords_case_insensitively(self) -> None:
        rule = Rule(
            rule_type="message_content",
            match_mode="contains_any",
            pattern="ＵＳＤＴ 搬砖\nSpam Offer",
            action="block",
        )
        message = SimpleNamespace(text="This is a SPAM OFFER", caption=None)
        user = User(id=1)
        self.assertTrue(rule_matches(rule, message, user))

    def test_detects_links_from_entities_without_storing_content(self) -> None:
        message = SimpleNamespace(
            text="点这里",
            caption=None,
            entities=[SimpleNamespace(type="text_link")],
            caption_entities=None,
        )
        self.assertTrue(message_has_link(message))

    def test_detects_bot_command_with_username_suffix(self) -> None:
        message = SimpleNamespace(text="/help@RelayCatBot details")
        self.assertTrue(is_bot_command(message))


class SecretStoreTests(unittest.TestCase):
    def test_encrypts_managed_api_key_at_rest(self) -> None:
        encrypted = encrypt_secret("sk-private-value")
        self.assertNotIn("sk-private-value", encrypted)
        self.assertEqual(decrypt_secret(encrypted), "sk-private-value")


class WebValidationTests(unittest.TestCase):
    def test_requires_https_for_remote_ai_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            normalize_ai_base_url("http://api.example.com/v1")

    def test_accepts_local_http_provider(self) -> None:
        self.assertEqual(
            normalize_ai_base_url("http://127.0.0.1:11434/v1/chat/completions"),
            "http://127.0.0.1:11434/v1",
        )


class AIReplyClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_chat_completion_content(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer test-key")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "  收到，我来处理。  "}}]},
            )

        client = AIReplyClient(make_settings())
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            reply = await client.generate_reply(
                [{"role": "user", "content": "可以帮我吗？"}],
                "简洁回复",
            )
        finally:
            await client.close()
        self.assertEqual(reply, "收到，我来处理。")

    async def test_rejects_invalid_provider_response(self) -> None:
        client = AIReplyClient(make_settings())
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"choices": []})
            )
        )
        try:
            with self.assertRaises(AIResponseError):
                await client.generate_reply([], "简洁回复")
        finally:
            await client.close()

    async def test_returns_structured_ai_review(self) -> None:
        client = AIReplyClient(make_settings())
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "content": '{"decision":"block","category":"scam","confidence":0.94,"reason":"诱导转账"}'
                                }
                            }
                        ]
                    },
                )
            )
        )
        try:
            decision = await client.review_message("立即转账", "拦截诈骗")
        finally:
            await client.close()
        self.assertTrue(decision.should_block)
        self.assertEqual(decision.category, "scam")
        self.assertEqual(decision.confidence, 0.94)

    def test_rejects_unstructured_ai_review(self) -> None:
        with self.assertRaises(AIResponseError):
            parse_review_decision("block this message")


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

    async def test_rate_limit_and_repeated_interception_trigger_auto_ban(self) -> None:
        policy = ProtectionPolicy(
            rate_limit_enabled=True,
            messages_per_minute=2,
            auto_ban_enabled=True,
            auto_ban_threshold=2,
            auto_ban_window_minutes=10,
            auto_ban_duration_hours=24,
        )
        now = datetime(2026, 7, 21, 12, 0, 0)
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
        self.assertFalse(limited.auto_banned)
        self.assertTrue(banned.auto_banned)
        async with self.sessions() as session:
            user = await session.get(User, 8848)
            events = (
                await session.execute(
                    select(AuditLog.event_type).where(AuditLog.user_id == 8848)
                )
            ).scalars().all()
        self.assertTrue(user.is_banned)
        self.assertIsNotNone(user.banned_until)
        self.assertIn("rate_limited", events)
        self.assertIn("auto_ban", events)

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


class DatabaseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_security_columns_to_legacy_tables(self) -> None:
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
                await connection.run_sync(_migrate_existing_tables)

                def columns(sync_connection):
                    inspector = inspect(sync_connection)
                    return {
                        "users": {item["name"] for item in inspector.get_columns("users")},
                        "rules": {item["name"] for item in inspector.get_columns("rules")},
                    }

                migrated = await connection.run_sync(columns)
        finally:
            await engine.dispose()
        self.assertTrue({"banned_until", "ban_reason"} <= migrated["users"])
        self.assertTrue({"match_mode", "name"} <= migrated["rules"])
