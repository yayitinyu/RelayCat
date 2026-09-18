import asyncio
import os
import unittest

import httpx

os.environ.setdefault(
    "RELAYCAT_BOT_TOKEN",
    "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("RELAYCAT_ADMIN_ID", "123456789")
os.environ.setdefault("RELAYCAT_DB_URL", "sqlite+aiosqlite:///:memory:")

from app.services.turnstile import (  # noqa: E402
    TURNSTILE_ACTION,
    TurnstileUnavailable,
    content_security_policy,
    is_challenge_token,
    new_challenge_token,
    render_challenge_page,
    verify_turnstile_token,
)
from app.web.verification import _parse_content_length  # noqa: E402


class TurnstileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.challenge = new_challenge_token()

    def _verify(self, payload):
        async def flow():
            async def handler(request: httpx.Request) -> httpx.Response:
                body = __import__("json").loads(request.content)
                self.assertIn("idempotency_key", body)
                self.assertNotIn("secret", body)
                return httpx.Response(200, json=payload)

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await verify_turnstile_token(
                    client,
                    "https://worker.example/",
                    "browser-token",
                    challenge=self.challenge,
                    expected_hostname="relaycat.example.com",
                )

        return asyncio.run(flow())

    def test_siteverify_requires_all_bound_metadata(self) -> None:
        valid = {
            "success": True,
            "hostname": "relaycat.example.com",
            "action": TURNSTILE_ACTION,
            "cdata": self.challenge,
            "error-codes": [],
        }
        self.assertTrue(self._verify(valid).passed)
        for field, value, code in (
            ("hostname", "other.example.com", "hostname-mismatch"),
            ("action", "other", "action-mismatch"),
            ("cdata", "ts_wrong", "cdata-mismatch"),
        ):
            with self.subTest(field=field):
                payload = {**valid, field: value}
                decision = self._verify(payload)
                self.assertFalse(decision.passed)
                self.assertIn(code, decision.error_codes)

    def test_transport_failure_is_retryable(self) -> None:
        async def flow():
            async def handler(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(502, json={"success": False})

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                with self.assertRaises(TurnstileUnavailable):
                    await verify_turnstile_token(
                        client,
                        "https://worker.example/",
                        "browser-token",
                        challenge=self.challenge,
                        expected_hostname="relaycat.example.com",
                    )

        asyncio.run(flow())

    def test_page_uses_fragment_spin_marker_and_strict_csp(self) -> None:
        page = render_challenge_page("public-sitekey", "nonce")
        self.assertIn('data-action="turnstile-spin-v1"', page)
        self.assertIn("window.location.hash.slice(1)", page)
        self.assertIn("history.replaceState", page)
        self.assertNotIn(self.challenge, page)
        self.assertTrue(is_challenge_token(self.challenge))
        policy = content_security_policy("nonce", turnstile=True)
        self.assertIn("https://challenges.cloudflare.com", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_public_form_requires_a_valid_bounded_content_length(self) -> None:
        self.assertIsNone(_parse_content_length(None))
        self.assertIsNone(_parse_content_length("not-a-number"))
        self.assertIsNone(_parse_content_length("-1"))
        self.assertEqual(_parse_content_length("0"), 0)
        self.assertEqual(_parse_content_length("16384"), 16384)


if __name__ == "__main__":
    unittest.main()
