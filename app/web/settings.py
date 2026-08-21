from fastapi import APIRouter, Depends, Request

from app.database.core import AsyncSessionLocal
from app.services.protection import add_audit_log, get_protection_policy
from app.services.runtime_settings import (
    get_bool_setting,
    get_int_setting,
    upsert_settings,
)
from app.services.verification import CHALLENGE_TTL, LOCKOUT_TIME, MAX_ATTEMPTS
from app.web.common import (
    redirect_to_login,
    redirect_with_query,
    require_admin,
    required_int,
    template_context,
    templates,
)

router = APIRouter()


@router.get("/settings")
async def settings_page(request: Request, authenticated: bool = Depends(require_admin)):
    if not authenticated:
        return redirect_to_login()
    protection = await get_protection_policy()
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=template_context(
            request,
            page_title="防护设置",
            confirm_reply=await get_bool_setting("confirm_reply"),
            rate_limit_enabled=protection.rate_limit_enabled,
            messages_per_minute=protection.messages_per_minute,
            burst_messages=protection.burst_messages,
            burst_window_seconds=protection.burst_window_seconds,
            repeat_limit=protection.repeat_limit,
            repeat_window_minutes=protection.repeat_window_minutes,
            auto_ban_enabled=protection.auto_ban_enabled,
            auto_ban_threshold=protection.auto_ban_threshold,
            auto_ban_window_minutes=protection.auto_ban_window_minutes,
            auto_ban_duration_hours=protection.auto_ban_duration_hours,
            log_retention_days=await get_int_setting(
                "log_retention_days", 30, minimum=1, maximum=365
            ),
            verification_attempts=MAX_ATTEMPTS,
            verification_ttl_minutes=int(CHALLENGE_TTL.total_seconds() // 60),
            verification_lock_minutes=int(LOCKOUT_TIME.total_seconds() // 60),
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
        values = {
            "confirm_reply": "true" if form.get("confirm_reply") else "false",
            "rate_limit_enabled": (
                "true" if form.get("rate_limit_enabled") else "false"
            ),
            "messages_per_minute": str(
                required_int(form, "messages_per_minute", "每分钟消息数", 1, 300)
            ),
            "burst_messages": str(
                required_int(form, "burst_messages", "突发消息数", 2, 50)
            ),
            "burst_window_seconds": str(
                required_int(form, "burst_window_seconds", "突发窗口", 2, 60)
            ),
            "repeat_limit": str(
                required_int(form, "repeat_limit", "重复消息数", 2, 20)
            ),
            "repeat_window_minutes": str(
                required_int(form, "repeat_window_minutes", "重复检测窗口", 1, 1440)
            ),
            "auto_ban_enabled": ("true" if form.get("auto_ban_enabled") else "false"),
            "auto_ban_threshold": str(
                required_int(form, "auto_ban_threshold", "自动封禁触发次数", 2, 100)
            ),
            "auto_ban_window_minutes": str(
                required_int(form, "auto_ban_window_minutes", "封禁统计时段", 1, 1440)
            ),
            "auto_ban_duration_hours": str(
                required_int(form, "auto_ban_duration_hours", "封禁时长", 0, 8760)
            ),
            "log_retention_days": str(
                required_int(form, "log_retention_days", "日志保留天数", 1, 365)
            ),
        }
    except ValueError as exc:
        return redirect_with_query("/settings", error=str(exc))

    await upsert_settings(values)
    async with AsyncSessionLocal() as session:
        add_audit_log(
            session,
            event_type="settings_updated",
            outcome="saved",
            reason="防护设置已更新",
            details={
                "rate_limit_enabled": bool(form.get("rate_limit_enabled")),
                "auto_ban_enabled": bool(form.get("auto_ban_enabled")),
            },
        )
        await session.commit()
    return redirect_with_query("/settings", saved=1)
