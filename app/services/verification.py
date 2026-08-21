import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import secrets
from weakref import WeakValueDictionary

from app.database.core import AsyncSessionLocal
from app.database.models import User, VerificationChallenge, utc_now
from app.services.protection import add_audit_log

CHALLENGE_TTL = timedelta(minutes=5)
MIN_SOLVE_TIME = timedelta(seconds=2)
FAILURE_RESET = timedelta(minutes=30)
LOCKOUT_TIME = timedelta(minutes=30)
MAX_ATTEMPTS = 3
SEQUENCE_LENGTH = 3
OPTION_COUNT = 6

_EMOJIS = (
    "🍎",
    "🍋",
    "🍇",
    "🥝",
    "🌙",
    "⭐",
    "☂️",
    "🎈",
    "🚲",
    "🚕",
    "✈️",
    "🚀",
    "🐟",
    "🐢",
    "🦊",
    "🐼",
    "🎵",
    "🔔",
)
_random = secrets.SystemRandom()
_user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


@dataclass(frozen=True)
class VerificationOption:
    token: str
    label: str


@dataclass(frozen=True)
class VerificationPrompt:
    challenge_id: str
    options: tuple[VerificationOption, ...]
    remaining_labels: tuple[str, ...]
    completed_steps: int
    total_steps: int
    attempts_remaining: int
    expires_at: datetime


@dataclass(frozen=True)
class VerificationResult:
    status: str
    prompt: VerificationPrompt | None = None
    locked_until: datetime | None = None
    attempts_remaining: int = 0


def _lock_for(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def _new_tokens() -> list[str]:
    tokens: list[str] = []
    while len(tokens) < OPTION_COUNT:
        token = secrets.token_urlsafe(3)
        if token not in tokens:
            tokens.append(token)
    return tokens


def _populate_challenge(
    challenge: VerificationChallenge,
    *,
    chat_id: int,
    now: datetime,
    attempts: int,
    message_id: int | None = None,
) -> None:
    labels = _random.sample(_EMOJIS, OPTION_COUNT)
    tokens = _new_tokens()
    options = [
        {"token": token, "label": label}
        for token, label in zip(tokens, labels, strict=True)
    ]
    challenge.challenge_id = secrets.token_urlsafe(9)
    challenge.chat_id = chat_id
    challenge.message_id = message_id
    challenge.options_json = json.dumps(
        options, ensure_ascii=False, separators=(",", ":")
    )
    challenge.answers_json = json.dumps(
        _random.sample(tokens, SEQUENCE_LENGTH), separators=(",", ":")
    )
    challenge.current_step = 0
    challenge.attempts = attempts
    challenge.issued_at = now
    challenge.not_before = now + MIN_SOLVE_TIME
    challenge.expires_at = now + CHALLENGE_TTL
    challenge.locked_until = None


def _decode_challenge(
    challenge: VerificationChallenge,
) -> tuple[list[VerificationOption], list[str]] | None:
    try:
        raw_options = json.loads(challenge.options_json)
        answers = json.loads(challenge.answers_json)
        options = [
            VerificationOption(token=str(item["token"]), label=str(item["label"]))
            for item in raw_options
        ]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    tokens = {option.token for option in options}
    if (
        len(options) != OPTION_COUNT
        or len(tokens) != OPTION_COUNT
        or not isinstance(answers, list)
        or len(answers) != SEQUENCE_LENGTH
        or any(
            not isinstance(answer, str) or answer not in tokens for answer in answers
        )
    ):
        return None
    return options, answers


def _prompt(challenge: VerificationChallenge) -> VerificationPrompt | None:
    decoded = _decode_challenge(challenge)
    if decoded is None:
        return None
    options, answers = decoded
    labels_by_token = {option.token: option.label for option in options}
    remaining = tuple(
        labels_by_token[token] for token in answers[challenge.current_step :]
    )
    return VerificationPrompt(
        challenge_id=challenge.challenge_id,
        options=tuple(options),
        remaining_labels=remaining,
        completed_steps=challenge.current_step,
        total_steps=len(answers),
        attempts_remaining=max(0, MAX_ATTEMPTS - challenge.attempts),
        expires_at=challenge.expires_at,
    )


async def issue_challenge(
    user_id: int,
    chat_id: int,
    *,
    now: datetime | None = None,
) -> VerificationResult:
    timestamp = now or utc_now()
    async with _lock_for(user_id):
        async with AsyncSessionLocal() as session:
            challenge = await session.get(VerificationChallenge, user_id)
            if (
                challenge
                and challenge.locked_until
                and challenge.locked_until > timestamp
            ):
                return VerificationResult(
                    status="locked",
                    locked_until=challenge.locked_until,
                )

            attempts = 0
            if challenge is not None:
                if (
                    challenge.last_failed_at
                    and timestamp - challenge.last_failed_at <= FAILURE_RESET
                ):
                    attempts = challenge.attempts
                else:
                    challenge.last_failed_at = None
                _populate_challenge(
                    challenge,
                    chat_id=chat_id,
                    now=timestamp,
                    attempts=attempts,
                )
            else:
                challenge = VerificationChallenge(user_id=user_id)
                _populate_challenge(
                    challenge,
                    chat_id=chat_id,
                    now=timestamp,
                    attempts=0,
                )
                session.add(challenge)
            await session.commit()
            return VerificationResult(
                status="active",
                prompt=_prompt(challenge),
                attempts_remaining=MAX_ATTEMPTS - attempts,
            )


async def bind_challenge_message(
    user_id: int,
    challenge_id: str,
    message_id: int,
) -> bool:
    async with _lock_for(user_id):
        async with AsyncSessionLocal() as session:
            challenge = await session.get(VerificationChallenge, user_id)
            if challenge is None or challenge.challenge_id != challenge_id:
                return False
            challenge.message_id = message_id
            await session.commit()
            return True


async def _fail_challenge(
    session,
    challenge: VerificationChallenge,
    *,
    now: datetime,
    reason: str,
) -> VerificationResult:
    challenge.attempts += 1
    challenge.last_failed_at = now
    add_audit_log(
        session,
        event_type="verification_failed",
        outcome="blocked",
        user_id=challenge.user_id,
        reason=reason,
        details={"attempt": challenge.attempts, "limit": MAX_ATTEMPTS},
        created_at=now,
    )
    if challenge.attempts >= MAX_ATTEMPTS:
        challenge.locked_until = now + LOCKOUT_TIME
        challenge.expires_at = now
        add_audit_log(
            session,
            event_type="verification_locked",
            outcome="blocked",
            user_id=challenge.user_id,
            reason="人机验证失败次数过多",
            details={"lockout_minutes": int(LOCKOUT_TIME.total_seconds() // 60)},
            created_at=now,
        )
        await session.commit()
        return VerificationResult(
            status="locked",
            locked_until=challenge.locked_until,
        )

    message_id = challenge.message_id
    _populate_challenge(
        challenge,
        chat_id=challenge.chat_id,
        now=now,
        attempts=challenge.attempts,
        message_id=message_id,
    )
    challenge.last_failed_at = now
    await session.commit()
    return VerificationResult(
        status="retry",
        prompt=_prompt(challenge),
        attempts_remaining=MAX_ATTEMPTS - challenge.attempts,
    )


async def verify_choice(
    *,
    user_id: int,
    chat_id: int,
    message_id: int,
    challenge_id: str,
    choice: str,
    now: datetime | None = None,
) -> VerificationResult:
    timestamp = now or utc_now()
    async with _lock_for(user_id):
        async with AsyncSessionLocal() as session:
            challenge = await session.get(VerificationChallenge, user_id)
            if (
                challenge is None
                or challenge.challenge_id != challenge_id
                or challenge.chat_id != chat_id
                or challenge.message_id != message_id
            ):
                return VerificationResult(status="invalid")
            if challenge.locked_until and challenge.locked_until > timestamp:
                return VerificationResult(
                    status="locked",
                    locked_until=challenge.locked_until,
                )
            if challenge.expires_at <= timestamp:
                return VerificationResult(status="expired")

            decoded = _decode_challenge(challenge)
            if decoded is None:
                await session.delete(challenge)
                await session.commit()
                return VerificationResult(status="invalid")
            _, answers = decoded
            if challenge.current_step >= len(answers):
                return VerificationResult(status="invalid")
            if not secrets.compare_digest(choice, answers[challenge.current_step]):
                return await _fail_challenge(
                    session,
                    challenge,
                    now=timestamp,
                    reason="点击顺序不正确",
                )

            challenge.current_step += 1
            if challenge.current_step < len(answers):
                await session.commit()
                return VerificationResult(
                    status="progress",
                    prompt=_prompt(challenge),
                    attempts_remaining=MAX_ATTEMPTS - challenge.attempts,
                )
            if timestamp < challenge.not_before:
                challenge.current_step = 0
                return await _fail_challenge(
                    session,
                    challenge,
                    now=timestamp,
                    reason="完成速度异常",
                )

            user = await session.get(User, user_id)
            if user is None:
                await session.delete(challenge)
                await session.commit()
                return VerificationResult(status="invalid")
            user.is_verified = True
            add_audit_log(
                session,
                event_type="verification_passed",
                outcome="verified",
                user_id=user_id,
                username=user.username,
                reason="完成一次性顺序挑战",
                created_at=timestamp,
            )
            await session.delete(challenge)
            await session.commit()
            return VerificationResult(status="passed")
