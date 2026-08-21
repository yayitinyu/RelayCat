import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import logging
from typing import Any
from weakref import WeakValueDictionary

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.core import AsyncSessionLocal
from app.database.models import AuditLog, User, utc_now
from app.services.filtering import compact_text, normalize_text
from app.services.runtime_settings import get_bool_setting, get_int_setting

logger = logging.getLogger(__name__)
STRIKE_EVENTS = {"rule_blocked", "rate_limited", "verification_locked"}
_user_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


@dataclass(frozen=True)
class ProtectionPolicy:
    rate_limit_enabled: bool = True
    messages_per_minute: int = 20
    burst_messages: int = 5
    burst_window_seconds: int = 10
    repeat_limit: int = 3
    repeat_window_minutes: int = 10
    auto_ban_enabled: bool = True
    auto_ban_threshold: int = 5
    auto_ban_window_minutes: int = 10
    auto_ban_duration_hours: int = 24


@dataclass(frozen=True)
class ProtectionResult:
    blocked: bool = False
    auto_banned: bool = False
    banned_until: datetime | None = None
    reason: str | None = None


async def get_protection_policy() -> ProtectionPolicy:
    return ProtectionPolicy(
        rate_limit_enabled=await get_bool_setting("rate_limit_enabled", True),
        messages_per_minute=await get_int_setting(
            "messages_per_minute", 20, minimum=1, maximum=300
        ),
        burst_messages=await get_int_setting(
            "burst_messages", 5, minimum=2, maximum=50
        ),
        burst_window_seconds=await get_int_setting(
            "burst_window_seconds", 10, minimum=2, maximum=60
        ),
        repeat_limit=await get_int_setting("repeat_limit", 3, minimum=2, maximum=20),
        repeat_window_minutes=await get_int_setting(
            "repeat_window_minutes", 10, minimum=1, maximum=1440
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


def fingerprint_content(value: str) -> str | None:
    normalized = (compact_text(value) or normalize_text(value))[:8000]
    if not normalized:
        return None
    return hmac.new(
        settings.secret_key.get_secret_value().encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


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
    content_fingerprint: str | None = None,
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
        content_fingerprint=content_fingerprint,
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
        result = await session.execute(
            delete(AuditLog).where(AuditLog.created_at < cutoff)
        )
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
    reason: str | None = None,
) -> ProtectionResult:
    if not policy.auto_ban_enabled:
        return ProtectionResult(blocked=True, reason=reason)
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
        return ProtectionResult(blocked=True, reason=reason)

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
        return ProtectionResult(blocked=True, reason=reason)

    user.is_banned = True
    user.banned_until = banned_until
    user.ban_reason = (
        f"在 {policy.auto_ban_window_minutes} 分钟内触发 {strike_count} 次拦截"
    )
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
    return ProtectionResult(
        blocked=True,
        auto_banned=True,
        banned_until=banned_until,
        reason=reason,
    )


async def record_message_and_check_rate_limit(
    *,
    user_id: int,
    username: str | None,
    message_type: str,
    content_fingerprint: str | None = None,
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
                content_fingerprint=content_fingerprint,
                created_at=timestamp,
            )
            await session.flush()
            if not current_policy.rate_limit_enabled:
                await session.commit()
                return ProtectionResult()

            minute_since = timestamp - timedelta(minutes=1)
            message_count = (
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.user_id == user_id,
                        AuditLog.event_type == "message_received",
                        AuditLog.created_at >= minute_since,
                    )
                )
                or 0
            )

            burst_since = timestamp - timedelta(
                seconds=current_policy.burst_window_seconds
            )
            burst_count = (
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.user_id == user_id,
                        AuditLog.event_type == "message_received",
                        AuditLog.created_at >= burst_since,
                    )
                )
                or 0
            )
            repeat_count = 0
            if content_fingerprint:
                repeat_since = timestamp - timedelta(
                    minutes=current_policy.repeat_window_minutes
                )
                repeat_count = (
                    await session.scalar(
                        select(func.count(AuditLog.id)).where(
                            AuditLog.user_id == user_id,
                            AuditLog.event_type == "message_received",
                            AuditLog.content_fingerprint == content_fingerprint,
                            AuditLog.created_at >= repeat_since,
                        )
                    )
                    or 0
                )

            reason: str | None = None
            details: dict[str, int] = {}
            if burst_count > current_policy.burst_messages:
                reason = "短时间发送过快"
                details = {
                    "count": burst_count,
                    "limit": current_policy.burst_messages,
                    "window_seconds": current_policy.burst_window_seconds,
                }
            elif message_count > current_policy.messages_per_minute:
                reason = "超过每分钟发送上限"
                details = {
                    "count": message_count,
                    "limit": current_policy.messages_per_minute,
                }
            elif content_fingerprint and repeat_count > current_policy.repeat_limit:
                reason = "短时间重复发送相同内容"
                details = {
                    "count": repeat_count,
                    "limit": current_policy.repeat_limit,
                    "window_minutes": current_policy.repeat_window_minutes,
                }

            if reason is None:
                await session.commit()
                return ProtectionResult()

            add_audit_log(
                session,
                event_type="rate_limited",
                outcome="blocked",
                user_id=user_id,
                username=username,
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
                reason=reason,
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
                reason=reason,
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
