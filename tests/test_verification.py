import json
import os
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault(
    "RELAYCAT_BOT_TOKEN",
    "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
)
os.environ.setdefault("RELAYCAT_ADMIN_ID", "123456789")
os.environ.setdefault("RELAYCAT_DB_URL", "sqlite+aiosqlite:///:memory:")

from app.database.models import (  # noqa: E402
    AuditLog,
    Base,
    User,
    VerificationChallenge,
)
from app.services.verification import (  # noqa: E402
    apply_turnstile_attempt,
    bind_challenge_message,
    get_turnstile_challenge,
    issue_challenge,
    verify_choice,
)
from app.services.turnstile import TURNSTILE_KIND, is_challenge_token  # noqa: E402


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(User(id=42, username="new-user", is_verified=False))
            await session.commit()
        self.now = datetime(2026, 8, 21, 8, 0, 0)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _challenge(self) -> VerificationChallenge:
        async with self.sessions() as session:
            challenge = await session.get(VerificationChallenge, 42)
            self.assertIsNotNone(challenge)
            return challenge

    async def test_challenge_is_bound_single_use_and_marks_user_verified(self) -> None:
        with patch("app.services.verification.AsyncSessionLocal", self.sessions):
            issued = await issue_challenge(42, 42, now=self.now)
            self.assertEqual(issued.status, "active")
            self.assertIsNotNone(issued.prompt)
            challenge_id = issued.prompt.challenge_id
            self.assertTrue(await bind_challenge_message(42, challenge_id, 100))

            invalid = await verify_choice(
                user_id=42,
                chat_id=42,
                message_id=999,
                challenge_id=challenge_id,
                choice="invalid",
                now=self.now + timedelta(seconds=3),
            )
            self.assertEqual(invalid.status, "invalid")

            answers = json.loads((await self._challenge()).answers_json)
            statuses = []
            for answer in answers:
                result = await verify_choice(
                    user_id=42,
                    chat_id=42,
                    message_id=100,
                    challenge_id=challenge_id,
                    choice=answer,
                    now=self.now + timedelta(seconds=3),
                )
                statuses.append(result.status)

            replay = await verify_choice(
                user_id=42,
                chat_id=42,
                message_id=100,
                challenge_id=challenge_id,
                choice=answers[-1],
                now=self.now + timedelta(seconds=4),
            )

        self.assertEqual(statuses, ["progress", "progress", "passed"])
        self.assertEqual(replay.status, "invalid")
        async with self.sessions() as session:
            user = await session.get(User, 42)
            challenge = await session.get(VerificationChallenge, 42)
        self.assertTrue(user.is_verified)
        self.assertIsNone(challenge)

    async def test_too_fast_completion_rotates_challenge_and_counts_failure(
        self,
    ) -> None:
        with patch("app.services.verification.AsyncSessionLocal", self.sessions):
            issued = await issue_challenge(42, 42, now=self.now)
            challenge_id = issued.prompt.challenge_id
            await bind_challenge_message(42, challenge_id, 100)
            answers = json.loads((await self._challenge()).answers_json)
            results = []
            for answer in answers:
                results.append(
                    await verify_choice(
                        user_id=42,
                        chat_id=42,
                        message_id=100,
                        challenge_id=challenge_id,
                        choice=answer,
                        now=self.now + timedelta(seconds=1),
                    )
                )

        self.assertEqual(results[-1].status, "retry")
        self.assertEqual(results[-1].attempts_remaining, 2)
        self.assertNotEqual(results[-1].prompt.challenge_id, challenge_id)

    async def test_three_wrong_attempts_lock_challenge_and_log_event(self) -> None:
        with patch("app.services.verification.AsyncSessionLocal", self.sessions):
            result = await issue_challenge(42, 42, now=self.now)
            for attempt in range(3):
                prompt = result.prompt
                self.assertIsNotNone(prompt)
                await bind_challenge_message(42, prompt.challenge_id, 100)
                challenge = await self._challenge()
                answers = json.loads(challenge.answers_json)
                options = json.loads(challenge.options_json)
                wrong = next(
                    item["token"] for item in options if item["token"] != answers[0]
                )
                result = await verify_choice(
                    user_id=42,
                    chat_id=42,
                    message_id=100,
                    challenge_id=prompt.challenge_id,
                    choice=wrong,
                    now=self.now + timedelta(seconds=3 + attempt),
                )

        self.assertEqual(result.status, "locked")
        self.assertIsNotNone(result.locked_until)
        async with self.sessions() as session:
            events = (
                (
                    await session.execute(
                        select(AuditLog.event_type).where(AuditLog.user_id == 42)
                    )
                )
                .scalars()
                .all()
            )
        self.assertEqual(events.count("verification_failed"), 3)
        self.assertIn("verification_locked", events)

    async def test_turnstile_challenge_is_bound_and_consumed_once(self) -> None:
        with patch("app.services.verification.AsyncSessionLocal", self.sessions):
            issued = await issue_challenge(
                42,
                42,
                kind=TURNSTILE_KIND,
                public_url="https://relaycat.example.com",
                now=self.now,
            )
            self.assertEqual(issued.status, "active")
            self.assertEqual(issued.prompt.kind, TURNSTILE_KIND)
            self.assertTrue(is_challenge_token(issued.prompt.challenge_id))
            self.assertTrue(
                issued.prompt.url.endswith("#" + issued.prompt.challenge_id)
            )
            self.assertTrue(
                await bind_challenge_message(42, issued.prompt.challenge_id, 100)
            )

            active = await get_turnstile_challenge(
                issued.prompt.challenge_id, now=self.now + timedelta(seconds=1)
            )
            self.assertEqual(active.status, "active")
            failed = await apply_turnstile_attempt(
                issued.prompt.challenge_id,
                False,
                now=self.now + timedelta(seconds=2),
            )
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.attempts, 1)
            passed = await apply_turnstile_attempt(
                issued.prompt.challenge_id,
                True,
                now=self.now + timedelta(seconds=3),
            )
            replay = await apply_turnstile_attempt(
                issued.prompt.challenge_id,
                True,
                now=self.now + timedelta(seconds=4),
            )

        self.assertEqual(passed.status, "passed")
        self.assertEqual(replay.status, "invalid")
        async with self.sessions() as session:
            user = await session.get(User, 42)
            challenge = await session.get(VerificationChallenge, 42)
        self.assertTrue(user.is_verified)
        self.assertIsNone(challenge)

    async def test_choice_callback_cannot_consume_turnstile_challenge(self) -> None:
        with patch("app.services.verification.AsyncSessionLocal", self.sessions):
            issued = await issue_challenge(
                42,
                42,
                kind=TURNSTILE_KIND,
                public_url="https://relaycat.example.com",
                now=self.now,
            )
            await bind_challenge_message(42, issued.prompt.challenge_id, 100)
            result = await verify_choice(
                user_id=42,
                chat_id=42,
                message_id=100,
                challenge_id=issued.prompt.challenge_id,
                choice="anything",
                now=self.now + timedelta(seconds=3),
            )
        self.assertEqual(result.status, "invalid")


if __name__ == "__main__":
    unittest.main()
