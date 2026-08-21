from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.database.core import AsyncSessionLocal
from app.database.models import Rule
from app.services.filtering import (
    ACTION_LABELS,
    MATCH_MODE_LABELS,
    MESSAGE_TYPE_LABELS,
    RULE_TYPE_LABELS,
    validate_rule_values,
)
from app.services.protection import add_audit_log
from app.services.rule_presets import RULE_PRESETS
from app.web.common import (
    redirect_to_login,
    redirect_with_query,
    require_admin,
    template_context,
    templates,
)

router = APIRouter()


def validate_rule(
    rule_type: str,
    pattern: str,
    action: str,
    match_mode: str = "regex",
) -> str | None:
    return validate_rule_values(rule_type, pattern, action, match_mode)


@router.get("/rules")
async def rules_page(
    request: Request,
    error: str | None = None,
    authenticated: bool = Depends(require_admin),
):
    if not authenticated:
        return redirect_to_login()
    async with AsyncSessionLocal() as session:
        rules = (
            (await session.execute(select(Rule).order_by(Rule.id.desc())))
            .scalars()
            .all()
        )
    existing_presets = {rule.preset_id for rule in rules if rule.preset_id}
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
        return redirect_with_query("/rules", error="预置规则不存在")
    async with AsyncSessionLocal() as session:
        exists = await session.scalar(
            select(func.count(Rule.id)).where(Rule.preset_id == preset.preset_id)
        )
        if not exists:
            rule = Rule(
                name=preset.name,
                rule_type=preset.rule_type,
                match_mode=preset.match_mode,
                pattern=preset.pattern,
                action=preset.action,
                preset_id=preset.preset_id,
                preset_version=preset.version,
            )
            session.add(rule)
            await session.flush()
            add_audit_log(
                session,
                event_type="rule_created",
                outcome="saved",
                rule_id=rule.id,
                reason=f"预置规则：{preset.name}",
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
                reason=rule.name
                or RULE_TYPE_LABELS.get(rule.rule_type, rule.rule_type),
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
            rule.preset_id = None
            rule.preset_version = None
            add_audit_log(
                session,
                event_type="rule_updated",
                outcome="saved",
                rule_id=rule.id,
                reason=rule.name or RULE_TYPE_LABELS[rule_type],
            )
            await session.commit()
    return redirect_with_query("/rules", saved=1)
