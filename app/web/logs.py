from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from app.database.core import AsyncSessionLocal
from app.database.models import AuditLog
from app.web.common import (
    EVENT_LABELS,
    OUTCOME_LABELS,
    redirect_to_login,
    require_admin,
    template_context,
    templates,
)

router = APIRouter()


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
        total = (
            await session.scalar(select(func.count(AuditLog.id)).where(*conditions))
            or 0
        )
        entries = (
            (
                await session.execute(
                    select(AuditLog)
                    .where(*conditions)
                    .order_by(AuditLog.id.desc())
                    .offset((page - 1) * 50)
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
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
            outcome_labels=OUTCOME_LABELS,
        ),
    )
