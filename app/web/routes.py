from fastapi import APIRouter

from app.web import auth, backups, dashboard, logs, rules, settings, users, verification

router = APIRouter()

for subrouter in (
    auth.router,
    backups.router,
    dashboard.router,
    rules.router,
    logs.router,
    settings.router,
    users.router,
    verification.router,
):
    router.include_router(subrouter)
