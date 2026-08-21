import html
import logging
from math import ceil
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji
from aiogram.types import User as TgUser
from sqlalchemy import select, update

from app.bot.loader import bot, dp
from app.bot.verification import render_verification_challenge
from app.core.config import settings
from app.database.core import AsyncSessionLocal
from app.database.models import MessageRoute, User, utc_now
from app.services.filtering import detect_message_type, evaluate_rules, is_bot_command
from app.services.protection import (
    add_audit_log,
    fingerprint_content,
    log_event,
    record_interception,
    record_message_and_check_rate_limit,
    release_expired_ban,
)
from app.services.runtime_settings import get_bool_setting
from app.services.verification import (
    bind_challenge_message,
    issue_challenge,
    verify_choice,
)

router = Router(name="relay")
dp.include_router(router)
logger = logging.getLogger(__name__)


async def get_or_create_user(tg_user: TgUser) -> User:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, tg_user.id)
        if user is None:
            user = User(
                id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                is_verified=False,
            )
            session.add(user)
        elif (
            user.username != tg_user.username
            or user.first_name != tg_user.first_name
            or user.last_name != tg_user.last_name
        ):
            user.username = tg_user.username
            user.first_name = tg_user.first_name
            user.last_name = tg_user.last_name
        await session.commit()
        await session.refresh(user)
        return user


def _lockout_message(locked_until) -> str:
    remaining = max(1, ceil((locked_until - utc_now()).total_seconds() / 60))
    return f"尝试次数过多，请在约 {remaining} 分钟后再发送 /start。"


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.chat.type != "private" or message.from_user is None:
        return
    user = await get_or_create_user(message.from_user)
    if user.is_verified or user.id == settings.admin_id:
        await message.answer("验证已完成，直接发送消息即可。")
        return

    result = await issue_challenge(user.id, message.chat.id)
    if result.status == "locked" and result.locked_until:
        await message.answer(_lockout_message(result.locked_until))
        return
    if result.prompt is None:
        logger.error("Could not create verification prompt for user %s", user.id)
        await message.answer("暂时无法创建验证，请稍后重试。")
        return

    text, markup = render_verification_challenge(result.prompt)
    sent = await message.answer(text, reply_markup=markup)
    if not await bind_challenge_message(
        user.id, result.prompt.challenge_id, sent.message_id
    ):
        await sent.edit_text("验证已更新，请重新发送 /start。")


@router.callback_query(F.data.startswith("verify:"))
async def on_verify_callback(callback: CallbackQuery) -> None:
    data = callback.data or ""
    parts = data.split(":", 2)
    message = callback.message
    chat = getattr(message, "chat", None)
    message_id = getattr(message, "message_id", None)
    if len(parts) != 3 or chat is None or message_id is None:
        await callback.answer("验证请求无效。", show_alert=True)
        return

    result = await verify_choice(
        user_id=callback.from_user.id,
        chat_id=chat.id,
        message_id=message_id,
        challenge_id=parts[1],
        choice=parts[2],
    )
    try:
        if result.status == "passed":
            await callback.answer("验证完成")
            await message.edit_text("验证完成。现在可以发送消息。")
        elif result.status in {"progress", "retry"} and result.prompt:
            text, markup = render_verification_challenge(
                result.prompt,
                retry=result.status == "retry",
            )
            await callback.answer(
                "顺序不对，已重置" if result.status == "retry" else ""
            )
            await message.edit_text(text, reply_markup=markup)
        elif result.status == "locked" and result.locked_until:
            await callback.answer("验证已锁定", show_alert=True)
            await message.edit_text(_lockout_message(result.locked_until))
        elif result.status == "expired":
            await callback.answer("挑战已过期", show_alert=True)
            await message.edit_text("挑战已过期，请重新发送 /start。")
        else:
            await callback.answer("挑战无效或已被替换。", show_alert=True)
    except TelegramBadRequest:
        logger.info("Verification message %s could not be updated", message_id)


async def get_reply_target_id(message: Message) -> int | None:
    reply = message.reply_to_message
    if reply is None:
        return None

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MessageRoute.user_id).where(
                MessageRoute.admin_message_id == reply.message_id
            )
        )
        user_id = result.scalars().first()
        if user_id:
            return int(user_id)

    text = reply.text or reply.caption or ""
    match = re.search(r"ID:\s*(\d+)", text)
    if match:
        return int(match.group(1))

    origin = reply.forward_origin
    if origin and getattr(origin, "type", "") == "user":
        return origin.sender_user.id
    return None


@router.message(Command("ban"), F.from_user.id == settings.admin_id)
async def cmd_ban(message: Message) -> None:
    target_id = await _command_target(message)
    if target_id is None:
        await message.answer("用法：/ban <用户 ID>，或回复一条中继消息。")
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.id == target_id)
            .values(is_banned=True, banned_until=None, ban_reason="管理员手动封禁")
        )
        add_audit_log(
            session,
            event_type="manual_ban",
            outcome="banned",
            user_id=target_id,
            reason="Telegram 管理员命令",
        )
        await session.commit()
    await message.answer(f"已封禁用户 {target_id}。")


@router.message(Command("unban"), F.from_user.id == settings.admin_id)
async def cmd_unban(message: Message) -> None:
    target_id = await _command_target(message)
    if target_id is None:
        await message.answer("用法：/unban <用户 ID>，或回复一条中继消息。")
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.id == target_id)
            .values(is_banned=False, banned_until=None, ban_reason=None)
        )
        add_audit_log(
            session,
            event_type="manual_unban",
            outcome="unbanned",
            user_id=target_id,
            reason="Telegram 管理员命令",
        )
        await session.commit()
    await message.answer(f"已解除用户 {target_id} 的封禁。")


async def _command_target(message: Message) -> int | None:
    parts = (message.text or "").split()
    if len(parts) > 1 and parts[1].isdigit():
        return int(parts[1])
    if message.reply_to_message:
        return await get_reply_target_id(message)
    return None


def _fingerprint_source(message: Message, message_type: str) -> str:
    content = (message.text or message.caption or "").strip()
    if content:
        return f"{message_type}\0{content}"
    media = getattr(message, message_type, None)
    if isinstance(media, list) and media:
        media = media[-1]
    unique_id = getattr(media, "file_unique_id", None)
    return f"{message_type}\0{unique_id or ''}"


@router.message(F.chat.type == "private")
async def handle_user_message(message: Message) -> None:
    if message.from_user is None:
        return
    if message.from_user.id == settings.admin_id:
        if message.reply_to_message:
            await handle_admin_reply(message)
        return

    user = await get_or_create_user(message.from_user)
    if not user.is_verified:
        await message.answer("请先发送 /start 完成人机验证。")
        return
    if user.is_banned:
        if not await release_expired_ban(user.id):
            return
        user.is_banned = False
        user.banned_until = None

    message_type = detect_message_type(message)
    rate_result = await record_message_and_check_rate_limit(
        user_id=user.id,
        username=user.username,
        message_type=message_type,
        content_fingerprint=fingerprint_content(
            _fingerprint_source(message, message_type)
        ),
    )
    if rate_result.blocked:
        if rate_result.auto_banned:
            await message.answer("发送过于频繁，账号已被自动封禁。")
        else:
            await message.answer("发送过于频繁，请稍后再试。")
        return

    rule_match = await evaluate_rules(message, user)
    if rule_match.action in {"block", "drop"}:
        result = await record_interception(
            event_type="rule_blocked",
            user_id=user.id,
            username=user.username,
            message_type=message_type,
            rule_id=rule_match.rule_id,
            reason=rule_match.rule_name or "命中过滤规则",
            details={"action": rule_match.action},
        )
        if rule_match.action == "block":
            text = (
                "消息已拦截；账号因多次触发规则被自动封禁。"
                if result.auto_banned
                else "这条消息未通过安全规则。"
            )
            await message.answer(text)
        return

    if rule_match.rule_id is None and is_bot_command(message):
        await record_interception(
            event_type="rule_blocked",
            user_id=user.id,
            username=user.username,
            message_type=message_type,
            reason="未放行的 Bot 命令",
            details={"action": "drop"},
        )
        return
    if not settings.enable_forwarding:
        await message.answer("消息中继暂时关闭。")
        return

    try:
        forwarded = await message.forward(settings.admin_id)
        display_name = (
            " ".join(part for part in (user.first_name, user.last_name) if part).strip()
            or "未命名用户"
        )
        username = f"@{html.escape(user.username)}" if user.username else "无用户名"
        card = await bot.send_message(
            settings.admin_id,
            (
                f"<b>{html.escape(display_name)}</b> · {username}\n"
                f"ID: <code>{user.id}</code>\n"
                "回复此消息即可回传。"
            ),
            reply_to_message_id=forwarded.message_id,
        )
        async with AsyncSessionLocal() as session:
            session.add_all(
                [
                    MessageRoute(
                        user_id=user.id,
                        admin_message_id=forwarded.message_id,
                        user_message_id=message.message_id,
                    ),
                    MessageRoute(
                        user_id=user.id,
                        admin_message_id=card.message_id,
                        user_message_id=message.message_id,
                    ),
                ]
            )
            await session.commit()
        await log_event(
            event_type="relay_forwarded",
            outcome="delivered",
            user_id=user.id,
            username=user.username,
            message_type=message_type,
        )
    except Exception:
        logger.exception("Failed to forward message %s", message.message_id)
        await log_event(
            event_type="relay_forwarded",
            outcome="error",
            user_id=user.id,
            username=user.username,
            message_type=message_type,
            reason="转发给管理员失败",
        )


async def handle_admin_reply(message: Message) -> None:
    user_id = await get_reply_target_id(message)
    if user_id is None:
        await message.answer("找不到对应用户，无法回复。")
        return
    try:
        await message.copy_to(chat_id=user_id)
        if await get_bool_setting("confirm_reply"):
            await message.react([ReactionTypeEmoji(emoji="👍")])
    except Exception:
        logger.exception("Failed to relay admin reply to user %s", user_id)
        await message.reply("回复发送失败，请查看服务日志。")
