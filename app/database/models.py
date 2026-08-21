from datetime import UTC, datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    """Return naive UTC for compatibility with existing database columns."""
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)  # Telegram User ID
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    is_verified = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    banned_until = Column(DateTime, nullable=True)
    ban_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class MessageRoute(Base):
    __tablename__ = "message_routes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    admin_message_id = Column(BigInteger, index=True)
    user_message_id = Column(BigInteger)
    created_at = Column(DateTime, default=utc_now)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(String, nullable=True)


class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(String, default="message_content")
    pattern = Column(String, nullable=False)
    match_mode = Column(String(32), default="regex", nullable=False)
    name = Column(String(120), nullable=True)
    action = Column(String, default="block")
    is_active = Column(Boolean, default=True)
    preset_id = Column(String(64), nullable=True, index=True)
    preset_version = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_log_user_event_time", "user_id", "event_type", "created_at"),
        Index("ix_audit_log_event_time", "event_type", "created_at"),
        Index(
            "ix_audit_log_user_fingerprint_time",
            "user_id",
            "content_fingerprint",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(40), nullable=False)
    outcome = Column(String(32), nullable=False)
    user_id = Column(BigInteger, nullable=True, index=True)
    username = Column(String(255), nullable=True)
    rule_id = Column(Integer, nullable=True)
    message_type = Column(String(32), nullable=True)
    reason = Column(String(255), nullable=True)
    details = Column(Text, nullable=True)
    content_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)


class VerificationChallenge(Base):
    __tablename__ = "verification_challenges"

    user_id = Column(BigInteger, primary_key=True)
    challenge_id = Column(String(32), nullable=False, unique=True, index=True)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=True)
    options_json = Column(Text, nullable=False)
    answers_json = Column(Text, nullable=False)
    current_step = Column(Integer, default=0, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    issued_at = Column(DateTime, default=utc_now, nullable=False)
    not_before = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    locked_until = Column(DateTime, nullable=True)
    last_failed_at = Column(DateTime, nullable=True)
