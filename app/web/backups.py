from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.database.core import AsyncSessionLocal
from app.services.backups import (
    BackupError,
    create_backup,
    list_backups,
    resolve_backup,
    restore_backup,
)
from app.services.protection import add_audit_log
from app.web.common import (
    redirect_to_login,
    redirect_with_query,
    require_admin,
    template_context,
    templates,
)

router = APIRouter()


@router.get("/backups")
async def backups_page(
    request: Request,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    return templates.TemplateResponse(
        request=request,
        name="backups.html",
        context=template_context(
            request,
            page_title="备份",
            backups=await list_backups(),
        ),
    )


@router.post("/backups/create")
async def create_backup_route(
    request: Request,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    try:
        backup = await create_backup()
    except (BackupError, OSError) as exc:
        return redirect_with_query("/backups", error=str(exc))
    async with AsyncSessionLocal() as session:
        add_audit_log(
            session,
            event_type="backup_created",
            outcome="saved",
            reason="手动创建数据库备份",
            details={"name": backup.name, "counts": backup.counts},
        )
        await session.commit()
    return redirect_with_query("/backups", saved=backup.name)


@router.get("/backups/{name}/download")
async def download_backup(
    request: Request,
    name: str,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    try:
        path = resolve_backup(name)
    except (BackupError, OSError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/backups/{name}/restore")
async def restore_backup_route(
    request: Request,
    name: str,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    try:
        result = await restore_backup(name)
    except (BackupError, OSError, ValueError) as exc:
        return redirect_with_query("/backups", error=str(exc))
    async with AsyncSessionLocal() as session:
        add_audit_log(
            session,
            event_type="backup_restored",
            outcome="saved",
            reason="数据库备份已恢复",
            details={
                "name": result.restored.name,
                "rollback": result.rollback.name,
            },
        )
        await session.commit()
    return redirect_with_query(
        "/backups",
        restored=result.restored.name,
        rollback=result.rollback.name,
    )
