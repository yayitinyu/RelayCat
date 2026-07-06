from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from app.core.config import settings
from app.services.ai import AIReplyClient

# Initialize Bot
bot = Bot(
    token=settings.bot_token.get_secret_value(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

# Initialize Dispatcher
dp = Dispatcher()
ai_client = AIReplyClient(settings)
