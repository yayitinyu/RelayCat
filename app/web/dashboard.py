from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select, update

from app.database.core import AsyncSessionLocal
from app.database.models import AuditLog, MessageRoute, User, utc_now
from app.services.protection import STRIKE_EVENTS, add_audit_log
from app.web.common import (
    redirect_to_login,
    require_admin,
    template_context,
    templates,
)

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
async def healthcheck() -> JSONResponse:
    return JSONResponse({"status": "ok", "app": "RelayCat"})


@router.get("/")
async def dashboard(request: Request, authenticated: bool = Depends(require_admin)):
    if not authenticated:
        return redirect_to_login()

    today = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with AsyncSessionLocal() as session:
        user_count = await session.scalar(select(func.count(User.id))) or 0
        verified_count = (
            await session.scalar(
                select(func.count(User.id)).where(User.is_verified.is_(True))
            )
            or 0
        )
        message_count = await session.scalar(select(func.count(MessageRoute.id))) or 0
        blocked_today = (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.event_type.in_(STRIKE_EVENTS),
                    AuditLog.created_at >= today,
                )
            )
            or 0
        )
        users = (
            (
                await session.execute(
                    select(User).order_by(User.created_at.desc()).limit(10)
                )
            )
            .scalars()
            .all()
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=template_context(
            request,
            page_title="概览",
            user_count=user_count,
            verified_count=verified_count,
            message_count=message_count,
            blocked_today=blocked_today,
            users=users,
        ),
    )


@router.post("/users/ban")
async def ban_user(
    request: Request,
    user_id: int = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(is_banned=True, banned_until=None, ban_reason="管理员手动封禁")
        )
        add_audit_log(
            session,
            event_type="manual_ban",
            outcome="banned",
            user_id=user_id,
            reason="管理后台操作",
        )
        await session.commit()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/users/unban")
async def unban_user(
    request: Request,
    user_id: int = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(is_banned=False, banned_until=None, ban_reason=None)
        )
        add_audit_log(
            session,
            event_type="manual_unban",
            outcome="unbanned",
            user_id=user_id,
            reason="管理后台操作",
        )
        await session.commit()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
