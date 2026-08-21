from fastapi import APIRouter

from app.web import auth, dashboard, logs, rules, settings

router = APIRouter()

for subrouter in (
    auth.router,
    dashboard.router,
    rules.router,
    logs.router,
    settings.router,
):
    router.include_router(subrouter)
