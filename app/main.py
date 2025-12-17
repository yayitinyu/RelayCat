import asyncio
import logging
import uvicorn
from fastapi import FastAPI
from aiogram import types
from aiogram.fsm.storage.memory import MemoryStorage

from app.settings import settings
from app.bot.loader import bot, dp
from app.database.core import init_db
# Import handlers to register them
import app.bot.handlers
from app.web.routes import router as web_router
from aiogram.types import BotCommand

async def setup_bot_commands():
    commands = [
        BotCommand(command="start", description="Start interaction"),
        BotCommand(command="ban", description="[Admin] Ban user"),
        BotCommand(command="unban", description="[Admin] Unban user"),
    ]
    await bot.set_my_commands(commands)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="RelayCat Admin")
app.include_router(web_router)

@app.on_event("startup")
async def on_startup():
    logger.info("Starting RelayCat...")
    await init_db()
    await setup_bot_commands()
    
    # Start Bot Polling as a background task
    # In production with detailed webhooks, implementation differs.
    # For now, we run polling in the same loop for simplicity (or we can use webhook).
    # Since Docker usually runs one process, we can run polling via asyncio.create_task
    # BUT: running polling inside FastAPI startup is a common pattern for simple bots.
    asyncio.create_task(run_bot())

async def run_bot():
    logger.info("Bot polling started.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

@app.get("/")
async def root():
    return {"status": "ok", "app": "RelayCat"}

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080)
