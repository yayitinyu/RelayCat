import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from aiogram.types import BotCommand
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.bot.loader import bot, dp
from app.core.config import settings
from app.database.core import engine, init_db
from app.services.protection import cleanup_old_audit_logs
from app.web.routes import router as web_router

import app.bot.handlers  # noqa: E402,F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent


async def setup_bot_commands() -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="开始或重新验证"),
            BotCommand(command="ban", description="管理员：封禁用户"),
            BotCommand(command="unban", description="管理员：解除封禁"),
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
        await bot.session.close()
        await engine.dispose()
        logger.info("RelayCat stopped")


app = FastAPI(
    title="RelayCat Admin",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable"
        if request.url.path.startswith("/static/")
        else "no-store"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; font-src 'self'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(web_router)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
