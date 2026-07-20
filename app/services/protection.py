import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from typing import Any
from weakref import WeakValueDictionary

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.core import AsyncSessionLocal
from app.database.models import AuditLog, User, utc_now
from app.services.runtime_settings import get_bool_setting, get_int_setting

logger = logging.getLogger(__name__)
STRIKE_EVENTS = {"rule_blocked", "ai_blocked", "rate_limited"}
_user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


@dataclass(frozen=True)
class ProtectionPolicy:
    rate_limit_enabled: bool = True
    messages_per_minute: int = 20
    auto_ban_enabled: bool = True
    auto_ban_threshold: int = 5
    auto_ban_window_minutes: int = 10
    auto_ban_duration_hours: int = 24


@dataclass(frozen=True)
class ProtectionResult:
    blocked: bool = False
    auto_banned: bool = False
    banned_until: datetime | None = None


async def get_protection_policy() -> ProtectionPolicy:
    return ProtectionPolicy(
        rate_limit_enabled=await get_bool_setting("rate_limit_enabled", True),
        messages_per_minute=await get_int_setting(
            "messages_per_minute", 20, minimum=1, maximum=300
        ),
        auto_ban_enabled=await get_bool_setting("auto_ban_enabled", True),
        auto_ban_threshold=await get_int_setting(
            "auto_ban_threshold", 5, minimum=2, maximum=100
        ),
        auto_ban_window_minutes=await get_int_setting(
            "auto_ban_window_minutes", 10, minimum=1, maximum=1440
        ),
        auto_ban_duration_hours=await get_int_setting(
            "auto_ban_duration_hours", 24, minimum=0, maximum=8760
        ),
    )


def _safe_details(details: dict[str, Any] | None) -> str | None:
    if not details:
        return None
    serialized = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
    return serialized[:2000]


def add_audit_log(
    session: AsyncSession,
    *,
    event_type: str,
    outcome: str,
    user_id: int | None = None,
    username: str | None = None,
    rule_id: int | None = None,
    message_type: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> AuditLog:
    entry = AuditLog(
        event_type=event_type[:40],
        outcome=outcome[:32],
        user_id=user_id,
        username=(username or "")[:255] or None,
        rule_id=rule_id,
        message_type=(message_type or "")[:32] or None,
        reason=(reason or "")[:255] or None,
        details=_safe_details(details),
        created_at=created_at or utc_now(),
    )
    session.add(entry)
    return entry


async def log_event(**values: Any) -> None:
    try:
        async with AsyncSessionLocal() as session:
            add_audit_log(session, **values)
            await session.commit()
    except SQLAlchemyError:
        logger.exception("Could not persist audit event %s", values.get("event_type"))


async def cleanup_old_audit_logs(retention_days: int | None = None) -> int:
    days = retention_days or await get_int_setting(
        "log_retention_days", 30, minimum=1, maximum=365
    )
    cutoff = utc_now() - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        result = await session.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
        await session.commit()
    removed = result.rowcount or 0
    if removed:
        logger.info("Removed %s audit log entries older than %s days", removed, days)
    return removed


def _lock_for(user_id: int) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


async def _apply_auto_ban(
    session: AsyncSession,
    *,
    user_id: int,
    username: str | None,
    policy: ProtectionPolicy,
    now: datetime,
) -> ProtectionResult:
    if not policy.auto_ban_enabled:
        return ProtectionResult(blocked=True)
    since = now - timedelta(minutes=policy.auto_ban_window_minutes)
    strike_count = (
        await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.user_id == user_id,
                AuditLog.event_type.in_(STRIKE_EVENTS),
                AuditLog.created_at >= since,
            )
        )
        or 0
    )
    if strike_count < policy.auto_ban_threshold:
        return ProtectionResult(blocked=True)

    banned_until = (
        None
        if policy.auto_ban_duration_hours == 0
        else now + timedelta(hours=policy.auto_ban_duration_hours)
    )
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or (
        user.is_banned and (user.banned_until is None or user.banned_until > now)
    ):
        return ProtectionResult(blocked=True)

    user.is_banned = True
    user.banned_until = banned_until
    user.ban_reason = f"在 {policy.auto_ban_window_minutes} 分钟内触发 {strike_count} 次拦截"
    add_audit_log(
        session,
        event_type="auto_ban",
        outcome="banned",
        user_id=user_id,
        username=username,
        reason=user.ban_reason,
        details={
            "strike_count": strike_count,
            "window_minutes": policy.auto_ban_window_minutes,
            "duration_hours": policy.auto_ban_duration_hours,
        },
        created_at=now,
    )
    return ProtectionResult(blocked=True, auto_banned=True, banned_until=banned_until)


async def record_message_and_check_rate_limit(
    *,
    user_id: int,
    username: str | None,
    message_type: str,
    policy: ProtectionPolicy | None = None,
    now: datetime | None = None,
) -> ProtectionResult:
    current_policy = policy or await get_protection_policy()
    timestamp = now or utc_now()
    async with _lock_for(user_id):
        async with AsyncSessionLocal() as session:
            add_audit_log(
                session,
                event_type="message_received",
                outcome="received",
                user_id=user_id,
                username=username,
                message_type=message_type,
                created_at=timestamp,
            )
            await session.flush()
            if not current_policy.rate_limit_enabled:
                await session.commit()
                return ProtectionResult()

            since = timestamp - timedelta(minutes=1)
            message_count = (
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.user_id == user_id,
                        AuditLog.event_type == "message_received",
                        AuditLog.created_at >= since,
                    )
                )
                or 0
            )
            if message_count <= current_policy.messages_per_minute:
                await session.commit()
                return ProtectionResult()

            add_audit_log(
                session,
                event_type="rate_limited",
                outcome="blocked",
                user_id=user_id,
                username=username,
                message_type=message_type,
                reason="超过每分钟发送上限",
                details={
                    "count": message_count,
                    "limit": current_policy.messages_per_minute,
                },
                created_at=timestamp,
            )
            await session.flush()
            result = await _apply_auto_ban(
                session,
                user_id=user_id,
                username=username,
                policy=current_policy,
                now=timestamp,
            )
            await session.commit()
            return result


async def record_interception(
    *,
    event_type: str,
    user_id: int,
    username: str | None,
    message_type: str,
    reason: str,
    rule_id: int | None = None,
    details: dict[str, Any] | None = None,
    policy: ProtectionPolicy | None = None,
    now: datetime | None = None,
) -> ProtectionResult:
    if event_type not in STRIKE_EVENTS:
        raise ValueError(f"Unsupported interception event: {event_type}")
    current_policy = policy or await get_protection_policy()
    timestamp = now or utc_now()
    async with _lock_for(user_id):
        async with AsyncSessionLocal() as session:
            add_audit_log(
                session,
                event_type=event_type,
                outcome="blocked",
                user_id=user_id,
                username=username,
                rule_id=rule_id,
                message_type=message_type,
                reason=reason,
                details=details,
                created_at=timestamp,
            )
            await session.flush()
            result = await _apply_auto_ban(
                session,
                user_id=user_id,
                username=username,
                policy=current_policy,
                now=timestamp,
            )
            await session.commit()
            return result


async def release_expired_ban(user_id: int, now: datetime | None = None) -> bool:
    timestamp = now or utc_now()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if (
            user is None
            or not user.is_banned
            or user.banned_until is None
            or user.banned_until > timestamp
        ):
            return False
        user.is_banned = False
        user.banned_until = None
        user.ban_reason = None
        add_audit_log(
            session,
            event_type="auto_unban",
            outcome="unbanned",
            user_id=user_id,
            username=user.username,
            reason="临时封禁已到期",
            created_at=timestamp,
        )
        await session.commit()
        return True
