import asyncio

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.security import (
    COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    is_authenticated,
    verify_password,
)
from app.web.common import (
    redirect_to_login,
    require_admin,
    template_context,
    templates,
)

router = APIRouter()


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
        await asyncio.sleep(0.6)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=template_context(
                request,
                page_title="登录",
                error="管理员密码不正确",
            ),
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
        path="/",
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
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
