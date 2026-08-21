from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.security import get_csrf_token, is_authenticated, verify_csrf_token

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=TEMPLATE_DIR)

EVENT_LABELS = {
    "message_received": "收到消息",
    "verification_passed": "验证通过",
    "verification_failed": "验证失败",
    "verification_locked": "验证锁定",
    "rule_blocked": "规则拦截",
    "rate_limited": "频率拦截",
    "auto_ban": "自动封禁",
    "auto_unban": "自动解封",
    "manual_ban": "手动封禁",
    "manual_unban": "手动解封",
    "relay_forwarded": "消息中继",
    "settings_updated": "设置变更",
    "rule_created": "新增规则",
    "rule_updated": "编辑规则",
    "rule_deleted": "删除规则",
}
OUTCOME_LABELS = {
    "received": "收到",
    "verified": "通过",
    "blocked": "拦截",
    "banned": "封禁",
    "unbanned": "解封",
    "delivered": "送达",
    "saved": "保存",
    "enabled": "启用",
    "disabled": "停用",
    "deleted": "删除",
    "error": "失败",
}


async def require_admin(request: Request) -> bool:
    if not is_authenticated(request):
        return False
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        form = await request.form()
        if not verify_csrf_token(request, str(form.get("csrf_token") or "")):
            raise HTTPException(status_code=403, detail="表单已过期，请刷新后重试")
    return True


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


def redirect_with_query(path: str, **values: object) -> RedirectResponse:
    query = urlencode(
        {key: str(value) for key, value in values.items() if value is not None}
    )
    target = f"{path}?{query}" if query else path
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def template_context(request: Request, **values):
    return {
        "request": request,
        "active_path": request.url.path,
        "csrf_token": get_csrf_token(request),
        **values,
    }


def required_int(
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
