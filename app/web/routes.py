from pathlib import Path
import json
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.secret_store import encrypt_secret
from app.core.security import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    get_csrf_token,
    is_authenticated,
    verify_csrf_token,
    verify_password,
)
from app.database.core import AsyncSessionLocal
from app.database.models import (
    AuditLog,
    BusinessConnection,
    MessageRoute,
    Rule,
    User,
    utc_now,
)
from app.services.filtering import (
    ACTION_LABELS,
    DEFAULT_MODERATION_POLICY,
    MATCH_MODE_LABELS,
    MESSAGE_TYPE_LABELS,
    RULE_PRESETS,
    RULE_TYPE_LABELS,
    validate_rule_values,
)
from app.services.ai import AIConfigurationError, AIReplyClient, AIResponseError
from app.services.protection import STRIKE_EVENTS, add_audit_log, get_protection_policy
from app.services.runtime_settings import (
    AIProviderConfig,
    clean_ai_model_id,
    get_ai_provider_config,
    get_bool_setting,
    get_int_setting,
    get_saved_ai_models,
    get_settings,
    normalize_ai_models,
    upsert_settings,
)

router = APIRouter()
templates = Jinja2Templates(directory=Path("app/templates"))

EVENT_LABELS = {
    "message_received": "收到消息",
    "rule_blocked": "规则拦截",
    "ai_blocked": "AI 拦截",
    "ai_review": "AI 审查",
    "rate_limited": "频率拦截",
    "auto_ban": "自动封禁",
    "auto_unban": "自动解封",
    "manual_ban": "手动封禁",
    "manual_unban": "手动解封",
    "relay_forwarded": "消息中继",
    "business_ai_reply": "Business AI",
    "settings_updated": "设置变更",
    "model_catalog_updated": "模型目录更新",
    "rule_created": "新增规则",
    "rule_updated": "编辑规则",
    "rule_deleted": "删除规则",
}


async def require_admin(request: Request) -> bool:
    if not is_authenticated(request):
        return False
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        form = await request.form()
        if not verify_csrf_token(request, str(form.get("csrf_token") or "")):
            raise HTTPException(status_code=403, detail="表单已过期，请刷新页面后重试")
    return True


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def redirect_with_query(path: str, **values: object) -> RedirectResponse:
    query = urlencode({key: str(value) for key, value in values.items() if value is not None})
    target = f"{path}?{query}" if query else path
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def template_context(request: Request, **values):
    return {
        "request": request,
        "active_path": request.url.path,
        "csrf_token": get_csrf_token(request),
        **values,
    }


def validate_rule(
    rule_type: str,
    pattern: str,
    action: str,
    match_mode: str = "regex",
) -> str | None:
    return validate_rule_values(rule_type, pattern, action, match_mode)


def normalize_ai_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    if not raw or len(raw) > 500:
        raise ValueError("AI Base URL 必须为 1–500 个字符")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AI Base URL 必须是完整的 http(s) 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("AI Base URL 不能包含账号、查询参数或片段")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 AI 接口必须使用 HTTPS；HTTP 仅允许本机地址")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path.removesuffix("/chat/completions")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _required_int(
    form,
    name: str,
    label: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(form.get(name) or ""))
    except ValueError as exc:
        raise ValueError(f"{label}必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{label}必须在 {minimum}–{maximum} 之间")
    return value


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
async def logout(
    request: Request,
    authenticated: bool = Depends(require_admin),
) -> RedirectResponse:
    if not authenticated:
        return redirect_to_login()
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/")
async def dashboard(request: Request, authenticated: bool = Depends(require_admin)):
    if not authenticated:
        return redirect_to_login()

    today = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
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
        blocked_today = (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.event_type.in_(STRIKE_EVENTS), AuditLog.created_at >= today
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
    existing_presets = {rule.name for rule in rules if rule.name}
    return templates.TemplateResponse(
        request=request,
        name="rules.html",
        context=template_context(
            request,
            page_title="过滤规则",
            rules=rules,
            error=error,
            rule_presets=RULE_PRESETS,
            existing_presets=existing_presets,
            rule_type_labels=RULE_TYPE_LABELS,
            match_mode_labels=MATCH_MODE_LABELS,
            action_labels=ACTION_LABELS,
            message_type_labels=MESSAGE_TYPE_LABELS,
        ),
    )


@router.post("/rules/add")
async def add_rule(
    request: Request,
    name: str = Form(""),
    rule_type: str = Form(...),
    match_mode: str = Form("contains_any"),
    pattern: str = Form(...),
    action: str = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    if error := validate_rule(rule_type, pattern, action, match_mode):
        return redirect_with_query("/rules", error=error)
    clean_name = name.strip()[:120] or None
    async with AsyncSessionLocal() as session:
        rule = Rule(
            name=clean_name,
            rule_type=rule_type,
            match_mode=match_mode,
            pattern=pattern.strip(),
            action=action,
        )
        session.add(rule)
        await session.flush()
        add_audit_log(
            session,
            event_type="rule_created",
            outcome="saved",
            rule_id=rule.id,
            reason=clean_name or RULE_TYPE_LABELS[rule_type],
        )
        await session.commit()
    return redirect_with_query("/rules", saved=1)


@router.post("/rules/presets/add")
async def add_rule_preset(
    request: Request,
    preset_id: str = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    preset = RULE_PRESETS.get(preset_id)
    if preset is None:
        return redirect_with_query("/rules", error="推荐防护不存在")
    async with AsyncSessionLocal() as session:
        exists = await session.scalar(
            select(func.count(Rule.id)).where(Rule.name == preset["name"])
        )
        if not exists:
            rule = Rule(
                name=preset["name"],
                rule_type=preset["rule_type"],
                match_mode=preset["match_mode"],
                pattern=preset["pattern"],
                action=preset["action"],
            )
            session.add(rule)
            await session.flush()
            add_audit_log(
                session,
                event_type="rule_created",
                outcome="saved",
                rule_id=rule.id,
                reason=f"推荐防护：{preset['name']}",
            )
            await session.commit()
    return redirect_with_query("/rules", saved=1)


@router.post("/rules/delete")
async def delete_rule(
    request: Request,
    rule_id: int = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        rule = await session.get(Rule, rule_id)
        if rule:
            reason = rule.name or RULE_TYPE_LABELS.get(rule.rule_type, rule.rule_type)
            await session.delete(rule)
            add_audit_log(
                session,
                event_type="rule_deleted",
                outcome="deleted",
                rule_id=rule_id,
                reason=reason,
            )
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
            add_audit_log(
                session,
                event_type="rule_updated",
                outcome="enabled" if rule.is_active else "disabled",
                rule_id=rule.id,
                reason=rule.name or RULE_TYPE_LABELS.get(rule.rule_type, rule.rule_type),
            )
            await session.commit()
    return RedirectResponse("/rules", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/rules/update")
async def update_rule(
    request: Request,
    rule_id: int = Form(...),
    name: str = Form(""),
    rule_type: str = Form(...),
    match_mode: str = Form("regex"),
    pattern: str = Form(...),
    action: str = Form(...),
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    if error := validate_rule(rule_type, pattern, action, match_mode):
        return redirect_with_query("/rules", error=error)
    async with AsyncSessionLocal() as session:
        rule = await session.get(Rule, rule_id)
        if rule:
            rule.name = name.strip()[:120] or None
            rule.rule_type = rule_type
            rule.match_mode = match_mode
            rule.pattern = pattern.strip()
            rule.action = action
            add_audit_log(
                session,
                event_type="rule_updated",
                outcome="saved",
                rule_id=rule.id,
                reason=rule.name or RULE_TYPE_LABELS[rule_type],
            )
            await session.commit()
    return redirect_with_query("/rules", saved=1)


@router.get("/settings")
async def settings_page(request: Request, authenticated: bool = Depends(require_admin)):
    if not authenticated:
        return redirect_to_login()
    values = await get_settings(
        {
            "confirm_reply",
            "business_ai_enabled",
            "business_ai_prompt",
            "moderation_ai_enabled",
            "moderation_ai_policy",
            "moderation_ai_threshold",
            "rate_limit_enabled",
            "messages_per_minute",
            "auto_ban_enabled",
            "auto_ban_threshold",
            "auto_ban_window_minutes",
            "auto_ban_duration_hours",
            "log_retention_days",
            "ai_api_key_encrypted",
        }
    )
    provider = await get_ai_provider_config()
    saved_ai_models = await get_saved_ai_models(provider.model)
    protection = await get_protection_policy()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=template_context(
            request,
            page_title="系统设置",
            confirm_reply=await get_bool_setting("confirm_reply"),
            business_ai_enabled=await get_bool_setting(
                "business_ai_enabled", settings.ai_enabled
            ),
            business_ai_prompt=values["business_ai_prompt"] or settings.ai_system_prompt,
            moderation_ai_enabled=await get_bool_setting("moderation_ai_enabled", False),
            moderation_ai_policy=values["moderation_ai_policy"]
            or DEFAULT_MODERATION_POLICY,
            moderation_ai_threshold=await get_int_setting(
                "moderation_ai_threshold", 80, minimum=50, maximum=100
            ),
            rate_limit_enabled=protection.rate_limit_enabled,
            messages_per_minute=protection.messages_per_minute,
            auto_ban_enabled=protection.auto_ban_enabled,
            auto_ban_threshold=protection.auto_ban_threshold,
            auto_ban_window_minutes=protection.auto_ban_window_minutes,
            auto_ban_duration_hours=protection.auto_ban_duration_hours,
            log_retention_days=await get_int_setting(
                "log_retention_days", 30, minimum=1, maximum=365
            ),
            ai_configured=provider.is_configured,
            ai_key_source=provider.source,
            ai_has_managed_key=bool(values["ai_api_key_encrypted"]),
            ai_has_key=bool(provider.api_key),
            ai_model=provider.model,
            saved_ai_models=saved_ai_models,
            ai_base_url=provider.base_url,
            secret_key_ready=(
                settings.secret_key.get_secret_value() != "change-me-before-production"
            ),
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
    try:
        prompt = str(form.get("business_ai_prompt") or "").strip()
        moderation_policy = str(form.get("moderation_ai_policy") or "").strip()
        custom_model = str(form.get("ai_model_custom") or "").strip()
        model = clean_ai_model_id(custom_model or form.get("ai_model") or "")
        base_url = normalize_ai_base_url(str(form.get("ai_base_url") or ""))
        if not prompt or len(prompt) > 4000:
            raise ValueError("Business AI 提示词必须为 1–4000 个字符")
        if not moderation_policy or len(moderation_policy) > 2000:
            raise ValueError("AI 审查标准必须为 1–2000 个字符")
        saved_models = await get_saved_ai_models()
        saved_models = normalize_ai_models([model, *saved_models])

        values = {
            "confirm_reply": "true" if form.get("confirm_reply") else "false",
            "business_ai_enabled": (
                "true" if form.get("business_ai_enabled") else "false"
            ),
            "business_ai_prompt": prompt,
            "moderation_ai_enabled": (
                "true" if form.get("moderation_ai_enabled") else "false"
            ),
            "moderation_ai_policy": moderation_policy,
            "moderation_ai_threshold": str(
                _required_int(form, "moderation_ai_threshold", "AI 拦截置信度", 50, 100)
            ),
            "rate_limit_enabled": (
                "true" if form.get("rate_limit_enabled") else "false"
            ),
            "messages_per_minute": str(
                _required_int(form, "messages_per_minute", "每分钟消息数", 1, 300)
            ),
            "auto_ban_enabled": (
                "true" if form.get("auto_ban_enabled") else "false"
            ),
            "auto_ban_threshold": str(
                _required_int(form, "auto_ban_threshold", "自动封禁触发次数", 2, 100)
            ),
            "auto_ban_window_minutes": str(
                _required_int(form, "auto_ban_window_minutes", "统计时段", 1, 1440)
            ),
            "auto_ban_duration_hours": str(
                _required_int(form, "auto_ban_duration_hours", "封禁时长", 0, 8760)
            ),
            "log_retention_days": str(
                _required_int(form, "log_retention_days", "日志保留天数", 1, 365)
            ),
            "ai_base_url": base_url,
            "ai_model": model,
            "ai_models": json.dumps(saved_models, ensure_ascii=False),
        }

        api_key = str(form.get("ai_api_key") or "").strip()
        if api_key:
            if len(api_key) > 512:
                raise ValueError("AI API Key 不能超过 512 个字符")
            if settings.secret_key.get_secret_value() == "change-me-before-production":
                raise ValueError(
                    "保存 API Key 前，请先把 RELAYCAT_SECRET_KEY 改为随机长字符串并重启"
                )
            values["ai_api_key_encrypted"] = encrypt_secret(api_key)
        elif form.get("clear_ai_api_key"):
            values["ai_api_key_encrypted"] = ""
    except ValueError as exc:
        return redirect_with_query("/settings", error=str(exc))

    await upsert_settings(values)
    async with AsyncSessionLocal() as session:
        add_audit_log(
            session,
            event_type="settings_updated",
            outcome="saved",
            reason="安全、限流与 AI 设置已更新",
            details={
                "business_ai_enabled": bool(form.get("business_ai_enabled")),
                "moderation_ai_enabled": bool(form.get("moderation_ai_enabled")),
                "rate_limit_enabled": bool(form.get("rate_limit_enabled")),
                "auto_ban_enabled": bool(form.get("auto_ban_enabled")),
                "api_key_changed": bool(api_key or form.get("clear_ai_api_key")),
            },
        )
        await session.commit()
    return redirect_with_query("/settings", saved=1)


def _model_fetch_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return "API Key 无效，或该密钥没有读取模型列表的权限"
        if status_code == 404:
            return "这个渠道没有提供兼容的 /models 接口"
        return f"渠道返回 HTTP {status_code}，暂时无法获取模型"
    if isinstance(exc, httpx.RequestError):
        return "无法连接 AI 渠道，请检查 Base URL 和网络"
    if isinstance(exc, AIConfigurationError):
        return "请先填写 API Key 和 Base URL"
    if isinstance(exc, AIResponseError):
        return "渠道返回的模型列表格式不兼容或内容为空"
    return "获取模型失败，请检查渠道配置"


@router.post("/settings/ai/models/fetch")
async def fetch_ai_models(
    request: Request,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    form = await request.form()
    try:
        base_url = normalize_ai_base_url(str(form.get("ai_base_url") or ""))
        current_provider = await get_ai_provider_config()
        submitted_key = str(form.get("ai_api_key") or "").strip()
        if submitted_key and len(submitted_key) > 512:
            raise ValueError("AI API Key 不能超过 512 个字符")
        if submitted_key and (
            settings.secret_key.get_secret_value() == "change-me-before-production"
        ):
            raise ValueError(
                "保存 API Key 前，请先把 RELAYCAT_SECRET_KEY 改为随机长字符串并重启"
            )
        api_key = submitted_key or current_provider.api_key
        if not api_key:
            raise AIConfigurationError("AI API key and Base URL are required")

        selected = str(form.get("ai_model_custom") or "").strip()
        selected = selected or str(form.get("ai_model") or "").strip()
        if selected:
            selected = clean_ai_model_id(selected)
        client = AIReplyClient(settings)
        try:
            models = await client.list_models(
                AIProviderConfig(
                    base_url=base_url,
                    api_key=api_key,
                    model=selected or current_provider.model,
                    source="admin" if submitted_key else current_provider.source,
                )
            )
        finally:
            await client.close()
    except ValueError as exc:
        return redirect_with_query("/settings", error=str(exc))
    except (AIConfigurationError, AIResponseError, httpx.HTTPError) as exc:
        return redirect_with_query("/settings", error=_model_fetch_error(exc))

    active_model = selected if selected in models else models[0]
    values = {
        "ai_base_url": base_url,
        "ai_model": active_model,
        "ai_models": json.dumps(models, ensure_ascii=False),
    }
    if submitted_key:
        values["ai_api_key_encrypted"] = encrypt_secret(submitted_key)
    await upsert_settings(values)
    async with AsyncSessionLocal() as session:
        add_audit_log(
            session,
            event_type="model_catalog_updated",
            outcome="saved",
            reason=f"已获取并保存 {len(models)} 个渠道模型",
            details={"model_count": len(models), "api_key_changed": bool(submitted_key)},
        )
        await session.commit()
    return redirect_with_query("/settings", models_fetched=len(models))


@router.get("/logs")
async def logs_page(
    request: Request,
    event: str | None = None,
    page: int = 1,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    page = max(1, page)
    event_filter = event if event in EVENT_LABELS else None
    conditions = [AuditLog.event_type == event_filter] if event_filter else []
    async with AsyncSessionLocal() as session:
        total = await session.scalar(select(func.count(AuditLog.id)).where(*conditions)) or 0
        result = await session.execute(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.id.desc())
            .offset((page - 1) * 50)
            .limit(50)
        )
        entries = result.scalars().all()
    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context=template_context(
            request,
            page_title="安全日志",
            entries=entries,
            total=total,
            page=page,
            has_next=page * 50 < total,
            selected_event=event_filter,
            event_labels=EVENT_LABELS,
        ),
    )
