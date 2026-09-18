from datetime import timedelta
from math import ceil

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, or_, select

from app.core.config import settings
from app.database.core import AsyncSessionLocal
from app.database.models import (
    AuditLog,
    MessageRoute,
    User,
    VerificationChallenge,
    utc_now,
)
from app.services.protection import add_audit_log
from app.web.common import (
    EVENT_LABELS,
    OUTCOME_LABELS,
    redirect_to_login,
    redirect_with_query,
    require_admin,
    template_context,
    templates,
)

router = APIRouter()
PAGE_SIZE = 25
USER_STATUSES = {"all", "verified", "pending", "banned"}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _user_filters(query: str, user_status: str):
    filters = []
    if query:
        if query.isdigit():
            filters.append(User.id == int(query))
        else:
            pattern = f"%{_escape_like(query.casefold())}%"
            filters.append(
                or_(
                    func.lower(func.coalesce(User.username, "")).like(
                        pattern, escape="\\"
                    ),
                    func.lower(func.coalesce(User.first_name, "")).like(
                        pattern, escape="\\"
                    ),
                    func.lower(func.coalesce(User.last_name, "")).like(
                        pattern, escape="\\"
                    ),
                )
            )
    if user_status == "verified":
        filters.extend((User.is_verified.is_(True), User.is_banned.is_(False)))
    elif user_status == "pending":
        filters.extend((User.is_verified.is_(False), User.is_banned.is_(False)))
    elif user_status == "banned":
        filters.append(User.is_banned.is_(True))
    return filters


@router.get("/users")
async def users_page(
    request: Request,
    q: str = Query(default="", max_length=100),
    user_status: str = Query(default="all", alias="status"),
    page: int = Query(default=1, ge=1, le=100000),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    query = q.strip()
    if user_status not in USER_STATUSES:
        user_status = "all"
    filters = _user_filters(query, user_status)
    async with AsyncSessionLocal() as session:
        total = await session.scalar(select(func.count(User.id)).where(*filters)) or 0
        pages = max(1, ceil(total / PAGE_SIZE))
        current_page = min(page, pages)
        users = (
            (
                await session.execute(
                    select(User)
                    .where(*filters)
                    .order_by(User.updated_at.desc(), User.id.desc())
                    .offset((current_page - 1) * PAGE_SIZE)
                    .limit(PAGE_SIZE)
                )
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        request=request,
        name="users.html",
        context=template_context(
            request,
            page_title="用户",
            users=users,
            q=query,
            user_status=user_status,
            total=total,
            page=current_page,
            pages=pages,
            admin_id=settings.admin_id,
        ),
    )


@router.get("/users/{user_id}")
async def user_detail(
    request: Request,
    user_id: int,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        message_count = (
            await session.scalar(
                select(func.count(MessageRoute.id)).where(
                    MessageRoute.user_id == user_id
                )
            )
            or 0
        )
        events = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(AuditLog.user_id == user_id)
                    .order_by(AuditLog.created_at.desc())
                    .limit(30)
                )
            )
            .scalars()
            .all()
        )
    return templates.TemplateResponse(
        request=request,
        name="user_detail.html",
        context=template_context(
            request,
            page_title="用户详情",
            user=user,
            message_count=message_count,
            events=events,
            event_labels=EVENT_LABELS,
            outcome_labels=OUTCOME_LABELS,
            is_admin=user_id == settings.admin_id,
        ),
    )


async def _managed_user(session, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return user


def _protected_admin_redirect(user_id: int):
    if user_id == settings.admin_id:
        return redirect_with_query(f"/users/{user_id}", error="不能修改管理员账户")
    return None


@router.post("/users/{user_id}/ban")
async def ban_managed_user(
    request: Request,
    user_id: int,
    reason: str = Form(default="管理员手动封禁"),
    duration_hours: int = Form(default=0),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    if protected := _protected_admin_redirect(user_id):
        return protected
    reason = reason.strip() or "管理员手动封禁"
    if len(reason) > 255 or not 0 <= duration_hours <= 8760:
        return redirect_with_query(f"/users/{user_id}", error="封禁参数无效")
    now = utc_now()
    banned_until = now + timedelta(hours=duration_hours) if duration_hours else None
    async with AsyncSessionLocal() as session:
        user = await _managed_user(session, user_id)
        user.is_banned = True
        user.banned_until = banned_until
        user.ban_reason = reason
        user.updated_at = now
        add_audit_log(
            session,
            event_type="manual_ban",
            outcome="banned",
            user_id=user_id,
            username=user.username,
            reason=reason,
            details={"duration_hours": duration_hours},
            created_at=now,
        )
        await session.commit()
    return redirect_with_query(f"/users/{user_id}", saved="banned")


@router.post("/users/{user_id}/unban")
async def unban_managed_user(
    request: Request,
    user_id: int,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    if protected := _protected_admin_redirect(user_id):
        return protected
    now = utc_now()
    async with AsyncSessionLocal() as session:
        user = await _managed_user(session, user_id)
        user.is_banned = False
        user.banned_until = None
        user.ban_reason = None
        user.updated_at = now
        add_audit_log(
            session,
            event_type="manual_unban",
            outcome="unbanned",
            user_id=user_id,
            username=user.username,
            reason="管理后台操作",
            created_at=now,
        )
        await session.commit()
    return redirect_with_query(f"/users/{user_id}", saved="unbanned")


@router.post("/users/{user_id}/verify")
async def verify_managed_user(
    request: Request,
    user_id: int,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    now = utc_now()
    async with AsyncSessionLocal() as session:
        user = await _managed_user(session, user_id)
        user.is_verified = True
        user.updated_at = now
        await session.execute(
            delete(VerificationChallenge).where(
                VerificationChallenge.user_id == user_id
            )
        )
        add_audit_log(
            session,
            event_type="manual_verify",
            outcome="verified",
            user_id=user_id,
            username=user.username,
            reason="管理员手动通过验证",
            created_at=now,
        )
        await session.commit()
    return redirect_with_query(f"/users/{user_id}", saved="verified")


@router.post("/users/{user_id}/revoke-verification")
async def revoke_user_verification(
    request: Request,
    user_id: int,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    if protected := _protected_admin_redirect(user_id):
        return protected
    now = utc_now()
    async with AsyncSessionLocal() as session:
        user = await _managed_user(session, user_id)
        user.is_verified = False
        user.updated_at = now
        await session.execute(
            delete(VerificationChallenge).where(
                VerificationChallenge.user_id == user_id
            )
        )
        add_audit_log(
            session,
            event_type="verification_revoked",
            outcome="blocked",
            user_id=user_id,
            username=user.username,
            reason="管理员撤销验证",
            created_at=now,
        )
        await session.commit()
    return redirect_with_query(f"/users/{user_id}", saved="revoked")


@router.post("/users/{user_id}/delete")
async def delete_managed_user(
    request: Request,
    user_id: int,
    authenticated: bool = Depends(require_admin),
) -> RedirectResponse:
    if not authenticated:
        return redirect_to_login()
    if protected := _protected_admin_redirect(user_id):
        return protected
    async with AsyncSessionLocal() as session:
        user = await _managed_user(session, user_id)
        username = user.username
        await session.execute(
            delete(MessageRoute).where(MessageRoute.user_id == user_id)
        )
        await session.execute(
            delete(VerificationChallenge).where(
                VerificationChallenge.user_id == user_id
            )
        )
        await session.delete(user)
        await session.flush()
        add_audit_log(
            session,
            event_type="user_deleted",
            outcome="deleted",
            user_id=user_id,
            username=username,
            reason="管理员删除用户资料",
        )
        await session.commit()
    return redirect_with_query("/users", saved="deleted")
