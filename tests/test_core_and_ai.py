import os
import unittest

import httpx
from pydantic import SecretStr, ValidationError

os.environ.setdefault(
    "RELAYCAT_BOT_TOKEN",
    "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("RELAYCAT_ADMIN_ID", "123456789")
os.environ.setdefault("RELAYCAT_DB_URL", "sqlite+aiosqlite:///:memory:")

from app.core.config import Settings  # noqa: E402
from app.services.ai import AIReplyClient, AIResponseError  # noqa: E402
from app.web.routes import validate_rule  # noqa: E402


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
