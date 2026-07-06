from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, index=True)  # Telegram User ID
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    is_verified = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class MessageRoute(Base):
    __tablename__ = "message_routes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, index=True)
    admin_message_id = Column(BigInteger, index=True) # ID of the message sent to Admin
    user_message_id = Column(BigInteger) # ID of the original message from User
    created_at = Column(DateTime, default=datetime.utcnow)

class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(String, nullable=True)

class BadWord(Base):
    __tablename__ = "bad_words"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    word = Column(String, unique=True, index=True)
    is_regex = Column(Boolean, default=False)

class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_type = Column(String, default="message_content") # username, message_content, is_command, is_forwarded
    pattern = Column(String, nullable=False)
    action = Column(String, default="block") # block, drop, allow
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BusinessConnection(Base):
    __tablename__ = "business_connections"

    id = Column(String, primary_key=True)
    account_user_id = Column(BigInteger, nullable=False, index=True)
    user_chat_id = Column(BigInteger, nullable=False)
    can_reply = Column(Boolean, default=False, nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "chat_id",
            "telegram_message_id",
            "role",
            name="uq_business_message_direction",
        ),
        Index("ix_conversation_lookup", "connection_id", "chat_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    connection_id = Column(
        String,
        ForeignKey("business_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_id = Column(BigInteger, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
