import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.core.security import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    is_authenticated,
    verify_password,
)
from app.database.core import AsyncSessionLocal
from app.database.models import BusinessConnection, MessageRoute, Rule, User
from app.services.runtime_settings import get_bool_setting, get_setting, upsert_settings

router = APIRouter()
templates = Jinja2Templates(directory=Path("app/templates"))

RULE_TYPES = {"message_content", "username", "is_command", "is_forwarded"}
RULE_ACTIONS = {"allow", "block", "drop"}


def require_admin(request: Request) -> bool:
    return is_authenticated(request)


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def template_context(request: Request, **values):
    return {"request": request, "active_path": request.url.path, **values}


def validate_rule(rule_type: str, pattern: str, action: str) -> str | None:
    if rule_type not in RULE_TYPES or action not in RULE_ACTIONS:
        return "规则类型或动作无效"
    if not pattern.strip() or len(pattern) > 500:
        return "规则内容必须为 1–500 个字符"
    try:
        re.compile(pattern)
    except re.error:
        return "正则表达式格式无效"
    return None


@router.get("/healthz", include_in_schema=False)
async def healthcheck() -> JSONResponse:
    return JSONResponse({"status": "ok", "app": "RelayCat"})


@router.get("/login")
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=template_context(request, page_title="登录"),
    )


@router.post("/login")
async def login(request: Request, password: str = Form(...)):
    if not verify_password(password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=template_context(request, page_title="登录", error="管理员密码不正确"),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_token(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/")
async def dashboard(request: Request, authenticated: bool = Depends(require_admin)):
    if not authenticated:
        return redirect_to_login()

    async with AsyncSessionLocal() as session:
        user_count = await session.scalar(select(func.count(User.id))) or 0
        msg_count = await session.scalar(select(func.count(MessageRoute.id))) or 0
        business_count = (
            await session.scalar(
                select(func.count(BusinessConnection.id)).where(
                    BusinessConnection.is_enabled.is_(True)
                )
            )
            or 0
        )
        result = await session.execute(
            select(User).order_by(User.created_at.desc()).limit(10)
        )
        users = result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=template_context(
            request,
            page_title="概览",
            user_count=user_count,
            msg_count=msg_count,
            business_count=business_count,
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
        await session.execute(update(User).where(User.id == user_id).values(is_banned=True))
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
        await session.execute(update(User).where(User.id == user_id).values(is_banned=False))
        await session.commit()
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/rules")
async def rules_page(
    request: Request,
    error: str | None = None,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Rule).order_by(Rule.id.desc()))
        rules = result.scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="rules.html",
        context=template_context(request, page_title="过滤规则", rules=rules, error=error),
    )


@router.post("/rules/add")
async def add_rule(
    request: Request,
    rule_type: str = Form(...),
    pattern: str = Form(...),
    action: str = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    if error := validate_rule(rule_type, pattern, action):
        return RedirectResponse(
            f"/rules?error={error}", status_code=status.HTTP_303_SEE_OTHER
        )
    async with AsyncSessionLocal() as session:
        session.add(Rule(rule_type=rule_type, pattern=pattern.strip(), action=action))
        await session.commit()
    return RedirectResponse("/rules", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/rules/delete")
async def delete_rule(
    request: Request,
    rule_id: int = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Rule).where(Rule.id == rule_id))
        await session.commit()
    return RedirectResponse("/rules", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/rules/toggle")
async def toggle_rule(
    request: Request,
    rule_id: int = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        rule = await session.get(Rule, rule_id)
        if rule:
            rule.is_active = not rule.is_active
            await session.commit()
    return RedirectResponse("/rules", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/rules/update")
async def update_rule(
    request: Request,
    rule_id: int = Form(...),
    rule_type: str = Form(...),
    pattern: str = Form(...),
    action: str = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    if error := validate_rule(rule_type, pattern, action):
        return RedirectResponse(
            f"/rules?error={error}", status_code=status.HTTP_303_SEE_OTHER
        )
    async with AsyncSessionLocal() as session:
        rule = await session.get(Rule, rule_id)
        if rule:
            rule.rule_type = rule_type
            rule.pattern = pattern.strip()
            rule.action = action
            await session.commit()
    return RedirectResponse("/rules", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/settings")
async def settings_page(request: Request, authenticated: bool = Depends(require_admin)):
    if not authenticated:
        return redirect_to_login()
    confirm_reply = await get_bool_setting("confirm_reply")
    business_ai_enabled = await get_bool_setting("business_ai_enabled", settings.ai_enabled)
    business_ai_prompt = await get_setting(
        "business_ai_prompt", settings.ai_system_prompt
    )
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=template_context(
            request,
            page_title="系统设置",
            confirm_reply=confirm_reply,
            business_ai_enabled=business_ai_enabled,
            business_ai_prompt=business_ai_prompt,
            ai_configured=settings.ai_api_key is not None,
            ai_model=settings.ai_model,
            ai_base_url=settings.ai_base_url,
        ),
    )


@router.post("/settings/update")
async def update_settings(
    request: Request,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    form = await request.form()
    prompt = str(form.get("business_ai_prompt") or "").strip()
    if not prompt or len(prompt) > 4000:
        return RedirectResponse(
            "/settings?error=提示词必须为 1–4000 个字符",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    await upsert_settings(
        {
            "confirm_reply": "true" if form.get("confirm_reply") else "false",
            "business_ai_enabled": (
                "true" if form.get("business_ai_enabled") else "false"
            ),
            "business_ai_prompt": prompt,
        }
    )
    return RedirectResponse("/settings?saved=1", status_code=status.HTTP_303_SEE_OTHER)
