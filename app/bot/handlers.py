import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import CallbackQuery, Message, ReactionTypeEmoji
from aiogram.types import User as TgUser
from sqlalchemy.future import select
from sqlalchemy import update

from app.bot.loader import bot, dp
from app.core.config import settings
from app.database.core import AsyncSessionLocal
from app.database.models import User, MessageRoute, Rule, Setting
from app.bot.verification import generate_verification_challenge
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
        await session.execute(update(User).where(User.id == target_id).values(is_banned=True))
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
        await session.execute(update(User).where(User.id == target_id).values(is_banned=False))
        await session.commit()
    
    await message.answer(f"✅ User {target_id} has been unbanned.")

async def check_rules(message: Message, user: User) -> str:
    """Returns 'allow', 'block', or 'drop'"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Rule).where(Rule.is_active.is_(True)))
        rules = result.scalars().all()
    
    for rule in rules:
        matched = False
        try:
            if rule.rule_type == "message_content":
                text = message.text or message.caption or ""
                if re.search(rule.pattern, text):
                    matched = True
            elif rule.rule_type == "username":
                if re.search(rule.pattern, user.username or ""):
                    matched = True
            elif rule.rule_type == "is_command":
                text = message.text or ""
                if text.startswith("/") and re.search(rule.pattern, text):
                    matched = True
            elif rule.rule_type == "is_forwarded":
                if message.forward_origin and rule.pattern.lower() == "true":
                    matched = True
        except re.error:
            logger.warning("Skipping invalid regex in rule %s", rule.id)
            continue

        if matched:
            return rule.action

    if (
        message.text
        and message.text.startswith("/")
        and message.from_user.id != settings.admin_id
    ):
        return "drop"
    return "allow"

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
        return # Ignore

    # Rule Check
    action = await check_rules(message, user)
    if action == "drop":
        return
    if action == "block":
        await message.answer("🚫 Message blocked by filter.")
        return

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
            
    except Exception:
        logger.exception("Failed to forward message %s", message.message_id)

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

