from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy import func, delete
from sqlalchemy.future import select

from app.database.core import AsyncSessionLocal
from app.database.models import User, MessageRoute, Rule, Setting
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
        
        user_count = user_count or 0
        msg_count = msg_count or 0

        # Recent users
        result = await session.execute(select(User).order_by(User.created_at.desc()).limit(10))
        users = result.scalars().all()
        
from app.database.models import User, MessageRoute, Rule, Setting
from sqlalchemy import delete

# ... (previous imports)

@router.get("/rules")
async def rules_page(request: Request, user=Depends(get_current_user)):
    if not user: return RedirectResponse("/login", status_code=303)
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Rule).order_by(Rule.id.desc()))
        rules = result.scalars().all()
    return templates.TemplateResponse("rules.html", {"request": request, "rules": rules})

@router.post("/rules/add")
async def add_rule(request: Request, rule_type: str = Form(...), pattern: str = Form(...), action: str = Form(...), user=Depends(get_current_user)):
    if not user: return RedirectResponse("/login", status_code=303)
    async with AsyncSessionLocal() as session:
        session.add(Rule(rule_type=rule_type, pattern=pattern.strip(), action=action))
        await session.commit()
    return RedirectResponse("/rules", status_code=303)

@router.post("/rules/delete")
async def delete_rule(request: Request, rule_id: int = Form(...), user=Depends(get_current_user)):
    if not user: return RedirectResponse("/login", status_code=303)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Rule).where(Rule.id == rule_id))
        await session.commit()
    return RedirectResponse("/rules", status_code=303)

@router.get("/settings")
async def settings_page(request: Request, user=Depends(get_current_user)):
    if not user: return RedirectResponse("/login", status_code=303)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Setting).where(Setting.key == "confirm_reply"))
        setting = res.scalar_one_or_none()
        confirm_reply = (setting.value == "true") if setting else False
    return templates.TemplateResponse("settings.html", {"request": request, "confirm_reply": confirm_reply})

@router.post("/settings/update")
async def update_settings(request: Request, user=Depends(get_current_user)):
    if not user: return RedirectResponse("/login", status_code=303)
    form = await request.form()
    confirm_reply = "true" if form.get("confirm_reply") else "false"
    
    async with AsyncSessionLocal() as session:
        # Upsert
        s = await session.execute(select(Setting).where(Setting.key == "confirm_reply"))
        setting = s.scalar_one_or_none()
        if not setting:
            session.add(Setting(key="confirm_reply", value=confirm_reply))
        else:
            setting.value = confirm_reply
        await session.commit()
    return RedirectResponse("/settings", status_code=303)
