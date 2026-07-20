import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import uvicorn
from aiogram.types import BotCommand
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.bot.loader import ai_client, bot, dp
from app.core.config import settings
from app.database.core import engine, init_db
from app.services.protection import cleanup_old_audit_logs
from app.web.routes import router as web_router

# Import modules for router registration.
import app.bot.business  # noqa: E402,F401
import app.bot.handlers  # noqa: E402,F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def setup_bot_commands() -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start interaction"),
            BotCommand(command="ban", description="[Admin] Ban user"),
            BotCommand(command="unban", description="[Admin] Unban user"),
        ]
    )


async def run_bot() -> None:
    logger.info("Telegram polling started")
    await bot.delete_webhook(drop_pending_updates=settings.drop_pending_updates)
    await dp.start_polling(bot)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await cleanup_old_audit_logs()
    if settings.admin_password.get_secret_value() == "admin":
        logger.warning("Default admin password is active; change it before deployment")
    if settings.secret_key.get_secret_value() == "change-me-before-production":
        logger.warning("Default session secret is active; change it before deployment")
    try:
        await setup_bot_commands()
    except Exception:
        logger.exception("Could not update Telegram commands; polling will still start")
    polling_task = asyncio.create_task(run_bot(), name="telegram-polling")
    logger.info("RelayCat started on %s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await polling_task
        await ai_client.close()
        await bot.session.close()
        await engine.dispose()
        logger.info("RelayCat stopped")


app = FastAPI(title="RelayCat Admin", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
