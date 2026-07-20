import asyncio
import logging
from weakref import WeakValueDictionary

import httpx
from aiogram import Router
from aiogram.enums import ChatAction, ChatType
from aiogram.types import BusinessConnection as TelegramBusinessConnection
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.bot.loader import ai_client, bot, dp
from app.core.config import settings
from app.database.core import AsyncSessionLocal
from app.database.models import BusinessConnection, ConversationMessage
from app.services.ai import AIConfigurationError, AIResponseError
from app.services.protection import log_event
from app.services.runtime_settings import (
    get_ai_provider_config,
    get_bool_setting,
    get_setting,
)

logger = logging.getLogger(__name__)
router = Router(name="business-automation")
dp.include_router(router)
_chat_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = WeakValueDictionary()


def _connection_can_reply(connection: TelegramBusinessConnection) -> bool:
    rights = getattr(connection, "rights", None)
    if rights is not None:
        return bool(getattr(rights, "can_reply", False))
    return bool(getattr(connection, "can_reply", False))


async def _save_connection(connection: TelegramBusinessConnection) -> BusinessConnection:
    async with AsyncSessionLocal() as session:
        record = await session.get(BusinessConnection, connection.id)
        values = {
            "account_user_id": connection.user.id,
            "user_chat_id": connection.user_chat_id,
            "can_reply": _connection_can_reply(connection),
            "is_enabled": connection.is_enabled,
        }
        if record is None:
            record = BusinessConnection(id=connection.id, **values)
            session.add(record)
        else:
            for key, value in values.items():
                setattr(record, key, value)
        await session.commit()
        await session.refresh(record)
        return record


async def _get_connection(connection_id: str) -> BusinessConnection:
    async with AsyncSessionLocal() as session:
        record = await session.get(BusinessConnection, connection_id)
    if record is not None:
        return record

    remote = await bot.get_business_connection(business_connection_id=connection_id)
    return await _save_connection(remote)


async def _store_message(
    connection_id: str,
    chat_id: int,
    message_id: int,
    role: str,
    content: str,
) -> bool:
    async with AsyncSessionLocal() as session:
        session.add(
            ConversationMessage(
                connection_id=connection_id,
                chat_id=chat_id,
                telegram_message_id=message_id,
                role=role,
                content=content,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
    return True


async def _load_history(connection_id: str, chat_id: int) -> list[dict[str, str]]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.connection_id == connection_id,
                ConversationMessage.chat_id == chat_id,
            )
            .order_by(ConversationMessage.id.desc())
            .limit(settings.ai_history_limit)
        )
        rows = list(reversed(result.scalars().all()))
    return [{"role": row.role, "content": row.content} for row in rows]


@router.business_connection()
async def handle_business_connection(connection: TelegramBusinessConnection) -> None:
    await _save_connection(connection)
    logger.info(
        "Business connection %s is now %s",
        connection.id,
        "enabled" if connection.is_enabled else "disabled",
    )


@router.business_message()
async def handle_business_message(message: Message) -> None:
    connection_id = message.business_connection_id
    if not connection_id or message.chat.type != ChatType.PRIVATE or not message.from_user:
        return

    connection = await _get_connection(connection_id)
    if message.from_user.id == connection.account_user_id:
        return
    if not connection.is_enabled or not connection.can_reply:
        return

    content = (message.text or message.caption or "").strip()
    if not content:
        return
    content = content[:8000]

    ai_enabled = await get_bool_setting("business_ai_enabled", settings.ai_enabled)
    if not ai_enabled:
        return

    lock_key = (connection_id, message.chat.id)
    lock = _chat_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _chat_locks[lock_key] = lock
    async with lock:
        inserted = await _store_message(
            connection_id,
            message.chat.id,
            message.message_id,
            "user",
            content,
        )
        if not inserted:
            return

        prompt = await get_setting("business_ai_prompt", settings.ai_system_prompt)
        assert prompt is not None
        provider = await get_ai_provider_config()
        try:
            await bot.send_chat_action(
                chat_id=message.chat.id,
                action=ChatAction.TYPING,
                business_connection_id=connection_id,
            )
            reply = await ai_client.generate_reply(
                await _load_history(connection_id, message.chat.id),
                prompt,
                provider,
            )
            sent = await bot.send_message(
                chat_id=message.chat.id,
                text=reply,
                parse_mode=None,
                business_connection_id=connection_id,
            )
            await _store_message(
                connection_id,
                message.chat.id,
                sent.message_id,
                "assistant",
                reply,
            )
            await log_event(
                event_type="business_ai_reply",
                outcome="delivered",
                user_id=message.from_user.id,
                username=message.from_user.username,
                message_type="text",
                details={"connection_id": connection_id},
            )
        except (AIConfigurationError, AIResponseError, httpx.HTTPError):
            logger.exception(
                "AI reply failed for business connection %s and chat %s",
                connection_id,
                message.chat.id,
            )
            await log_event(
                event_type="business_ai_reply",
                outcome="error",
                user_id=message.from_user.id,
                username=message.from_user.username,
                message_type="text",
                reason="Business AI 回复失败",
                details={"connection_id": connection_id},
            )
