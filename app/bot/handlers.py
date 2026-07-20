import html
import logging
import re

import httpx
from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji
from aiogram.types import User as TgUser
from sqlalchemy.future import select
from sqlalchemy import update

from app.bot.loader import ai_client, bot, dp
from app.core.config import settings
from app.database.core import AsyncSessionLocal
from app.database.models import User, MessageRoute, Setting
from app.bot.verification import generate_verification_challenge
from app.services.ai import AIConfigurationError, AIResponseError
from app.services.filtering import (
    DEFAULT_MODERATION_POLICY,
    detect_message_type,
    evaluate_rules,
    is_bot_command,
)
from app.services.protection import (
    add_audit_log,
    log_event,
    record_interception,
    record_message_and_check_rate_limit,
    release_expired_ban,
)
from app.services.runtime_settings import (
    get_ai_provider_config,
    get_bool_setting,
    get_int_setting,
    get_setting,
)
router = Router()
dp.include_router(router)
logger = logging.getLogger(__name__)

async def get_or_create_user(tg_user: TgUser):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == tg_user.id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
                is_verified=False
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        elif (
            user.username != tg_user.username
            or user.first_name != tg_user.first_name
            or user.last_name != tg_user.last_name
        ):
            user.username = tg_user.username
            user.first_name = tg_user.first_name
            user.last_name = tg_user.last_name
            await session.commit()
        return user

@router.message(CommandStart())
async def cmd_start(message: Message):
    if message.chat.type != 'private':
        return

    user = await get_or_create_user(message.from_user)
    
    if user.is_verified or message.from_user.id == settings.admin_id:
        await message.answer("Hello again! You are verified. Messages you send here will be forwarded to the admin.")
    else:
        # Start verification
        target, markup = generate_verification_challenge()
        # Store target in state or just check callback?
        # A simple stateless way is to encode target in callback data of correct answer, but that's insecure.
        # Better: We encode the target in the text instructions.
        await message.answer(
            f"Welcome! To prove you are human, please tap the {target} button below:",
            reply_markup=markup
        )

@router.callback_query(F.data.startswith("verify:"))
async def on_verify_callback(callback: CallbackQuery):
    emoji_clicked = callback.data.split(":")[1]
    # We need to know what the target was.
    # Parsing the message text is a hack but stateless and simple for this level.
    # Text: "Welcome! ... tap the 🍎 button below:"
    
    msg_text = callback.message.text
    if "tap the" not in msg_text:
        await callback.answer("Session expired or invalid.", show_alert=True)
        return
        
    target_emoji = msg_text.split("tap the")[1].strip().split(" ")[0]
    
    if emoji_clicked == target_emoji:
        # Verify user
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(User).where(User.id == callback.from_user.id).values(is_verified=True)
            )
            await session.commit()
            
        await callback.message.edit_text("✅ Verified! You can now send messages to the admin.")
    else:
        # Wrong answer, retry
        target, markup = generate_verification_challenge()
        await callback.message.edit_text(
            f"Wrong! Try again. Tap the {target}:",
            reply_markup=markup
        )


async def get_reply_target_id(message: Message) -> int | None:
    """
    Try to find the target user ID from a reply message.
    1. Check MessageRoute in DB (most reliable for active sessions)
    2. Check Info Card text (stateless fallback)
    3. Check Forward Origin (stateless fallback for forwards)
    """
    reply_msg = message.reply_to_message
    if not reply_msg:
        return None

    # 1. Check DB Route
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MessageRoute).where(MessageRoute.admin_message_id == reply_msg.message_id)
        )
        route = result.scalar_one_or_none()
        if route:
            return route.user_id

    # 2. Check Info Card Text (e.g. "ID: 123456")
    text = reply_msg.text or reply_msg.caption or ""
    # Look for "ID: 123456" pattern
    match = re.search(r"ID:\s*(\d+)", text)
    if match:
        return int(match.group(1))

    # 3. Check Forward Origin
    # Check if the message is a forward from a user
    if reply_msg.forward_origin and getattr(reply_msg.forward_origin, 'type', '') == 'user':
        return reply_msg.forward_origin.sender_user.id
        
    return None

# ---------- Admin Commands ----------
@router.message(Command("ban"), F.from_user.id == settings.admin_id)
async def cmd_ban(message: Message):
    # Extract ID from args or reply
    target_id = None
    args = message.text.split()
    
    if len(args) > 1 and args[1].isdigit():
        target_id = int(args[1])
    elif message.reply_to_message:
        target_id = await get_reply_target_id(message)
    
    if not target_id:
        await message.answer("⚠️ Usage: /ban <user_id> or reply to a user message.")
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
    
    await message.answer(f"🔒 User {target_id} has been banned.")

@router.message(Command("unban"), F.from_user.id == settings.admin_id)
async def cmd_unban(message: Message):
    target_id = None
    args = message.text.split()
    
    if len(args) > 1 and args[1].isdigit():
        target_id = int(args[1])
    elif message.reply_to_message:
        target_id = await get_reply_target_id(message)

    if not target_id:
        await message.answer("⚠️ Usage: /unban <user_id> or reply to a user message.")
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
    
    await message.answer(f"✅ User {target_id} has been unbanned.")

# ---------- Message Forwarding (User -> Admin) ----------
@router.message(F.chat.type == "private")
async def handle_user_message(message: Message):
    if message.from_user.id == settings.admin_id:
        if message.reply_to_message:
            await handle_admin_reply(message)
        return

    # Check verification
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == message.from_user.id))
        user = result.scalar_one_or_none()
        
    # Verification Check
    if message.from_user.id != settings.admin_id and (not user or not user.is_verified):
        await message.answer("Please type /start to verify yourself first.")
        return
        
    if user.is_banned:
        if await release_expired_ban(user.id):
            user.is_banned = False
            user.banned_until = None
        else:
            return

    message_type = detect_message_type(message)
    rate_result = await record_message_and_check_rate_limit(
        user_id=user.id,
        username=user.username,
        message_type=message_type,
    )
    if rate_result.blocked:
        if rate_result.auto_banned:
            await message.answer("🚫 发送过于频繁，账号已被自动封禁。")
        else:
            await message.answer("⏳ 发送太快了，请稍后再试。")
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
            if result.auto_banned:
                await message.answer("🚫 消息已拦截；因多次触发规则，账号已被自动封禁。")
            else:
                await message.answer("🚫 这条消息未通过安全规则。")
        return

    if rule_match.rule_id is None and is_bot_command(message):
        await record_interception(
            event_type="rule_blocked",
            user_id=user.id,
            username=user.username,
            message_type=message_type,
            reason="未在白名单中放行的 Bot 命令",
            details={"action": "drop"},
        )
        return

    # An explicit allow rule is a deliberate whitelist and skips AI review.
    if rule_match.rule_id is None and await get_bool_setting("moderation_ai_enabled", False):
        content = (message.text or message.caption or "").strip()
        if content:
            provider = await get_ai_provider_config()
            policy = await get_setting("moderation_ai_policy", DEFAULT_MODERATION_POLICY)
            threshold = await get_int_setting(
                "moderation_ai_threshold", 80, minimum=50, maximum=100
            )
            try:
                decision = await ai_client.review_message(
                    content,
                    policy or DEFAULT_MODERATION_POLICY,
                    provider,
                )
                if decision.should_block and decision.confidence * 100 >= threshold:
                    result = await record_interception(
                        event_type="ai_blocked",
                        user_id=user.id,
                        username=user.username,
                        message_type=message_type,
                        reason=decision.reason,
                        details={
                            "category": decision.category,
                            "confidence": round(decision.confidence, 3),
                        },
                    )
                    if result.auto_banned:
                        await message.answer("🚫 消息未通过 AI 安全审查；账号已被自动封禁。")
                    else:
                        await message.answer("🚫 这条消息未通过 AI 安全审查。")
                    return
                await log_event(
                    event_type="ai_review",
                    outcome="allowed",
                    user_id=user.id,
                    username=user.username,
                    message_type=message_type,
                    details={
                        "category": decision.category,
                        "confidence": round(decision.confidence, 3),
                    },
                )
            except (AIConfigurationError, AIResponseError, httpx.HTTPError):
                logger.exception("AI moderation failed for user %s", user.id)
                await log_event(
                    event_type="ai_review",
                    outcome="error",
                    user_id=user.id,
                    username=user.username,
                    message_type=message_type,
                    reason="AI 审查失败，已按安全设置放行",
                )

    if not settings.enable_forwarding:
        await message.answer("Message forwarding is temporarily disabled.")
        return

    # Forward to Admin
    # We use copy_message or forward_message. 
    # RelayCat original design: Forward message, then send metadata card.
    
    try:
        # Forward original
        fwd = await message.forward(settings.admin_id)
        
        # Send info card
        info_text = (
            f"👤 <b>User Info</b>\n"
            f"ID: <code>{user.id}</code>\n"
            f"Name: {html.escape(user.first_name or '')} {html.escape(user.last_name or '')}\n"
            f"Username: @{html.escape(user.username or 'none')}\n"
            f"<i>Reply to this or the forwarded message to answer.</i>"
        )
        card = await bot.send_message(settings.admin_id, info_text, reply_to_message_id=fwd.message_id)
        
        # Save route
        async with AsyncSessionLocal() as session:
            # Route for forwarding
            session.add(MessageRoute(
                user_id=user.id,
                admin_message_id=fwd.message_id,
                user_message_id=message.message_id
            ))
            # Route for card
            session.add(MessageRoute(
                user_id=user.id,
                admin_message_id=card.message_id,
                user_message_id=message.message_id
            ))
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

# ---------- Admin Reply (Admin -> User) ----------
async def handle_admin_reply(message: Message):
    # Check if reply is to a routed message
    user_id = await get_reply_target_id(message)
        
    if not user_id:
        await message.answer("⚠️ Route not found. Cannot reply to this message.")
        return
        
    # Send back to user
    try:
        # We use copy_message to preserve content type (text/photo/etc)
        await message.copy_to(chat_id=user_id)
        
        # Confirm Reply (Thumps Up) if enabled
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Setting).where(Setting.key == "confirm_reply"))
            setting = res.scalar_one_or_none()
            if setting and setting.value == "true":
                 await message.react([ReactionTypeEmoji(emoji="👍")])
            
    except Exception as e:
        await message.reply(f"❌ Failed to reach user: {e}")

