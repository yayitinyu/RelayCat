from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from app.settings import settings

# Initialize Bot
bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Initialize Dispatcher
dp = Dispatcher()

# Store bot instance in context for webhook access if needed
# (FastAPI will access 'bot' directly)
