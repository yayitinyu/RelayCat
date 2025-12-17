from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.future import select

from app.database.core import AsyncSessionLocal
from app.database.models import User, MessageRoute
from app.settings import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Simple dependency to check cookie/session
# For real prod, use signed cookies or JWT in cookie.
# Here we just use a simple signed cookie check manually for speed.

def get_current_user(request: Request):
    token = request.cookies.get("auth_token")
    if token == settings.SECRET_KEY: # Very simple "token" is just key for now
        return True
    return None

@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == settings.ADMIN_PASSWORD:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="auth_token", value=settings.SECRET_KEY)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"})

@router.get("/")
async def dashboard(request: Request, user=Depends(get_current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=303)
        
    async with AsyncSessionLocal() as session:
        # Stats
        user_count = await session.scalar(select(func.count(User.id)))
        msg_count = await session.scalar(select(func.count(MessageRoute.id)))
        
        # Recent users
        result = await session.execute(select(User).order_by(User.created_at.desc()).limit(10))
        users = result.scalars().all()
        
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user_count": user_count,
        "msg_count": msg_count,
        "users": users
    })
