import logging
import secrets
from typing import cast

import httpx
from aiogram.exceptions import TelegramAPIError
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.bot.loader import bot
from app.core.config import settings
from app.services.turnstile import (
    TurnstileUnavailable,
    content_security_policy,
    is_challenge_token,
    render_challenge_page,
    render_result_page,
    verify_turnstile_token,
)
from app.services.verification import (
    apply_turnstile_attempt,
    get_turnstile_challenge,
)

router = APIRouter()
logger = logging.getLogger(__name__)
MAX_CHALLENGE_BODY = 16 * 1024


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _html_response(
    body: str,
    *,
    nonce: str,
    turnstile: bool,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Security-Policy": content_security_policy(
                nonce, turnstile=turnstile
            ),
        },
    )


def _require_turnstile() -> tuple[str, str, str]:
    if not settings.turnstile_configured:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return (
        cast(str, settings.turnstile_sitekey),
        cast(str, settings.turnstile_verify_url),
        cast(str, settings.turnstile_hostname),
    )


@router.get("/verify", response_class=HTMLResponse)
async def turnstile_page() -> HTMLResponse:
    sitekey, _, _ = _require_turnstile()
    nonce = secrets.token_urlsafe(18)
    return _html_response(
        render_challenge_page(sitekey, nonce),
        nonce=nonce,
        turnstile=True,
    )


@router.post("/verify", response_class=HTMLResponse)
async def complete_turnstile(request: Request) -> HTMLResponse:
    sitekey, verify_url, expected_hostname = _require_turnstile()
    nonce = secrets.token_urlsafe(18)
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/x-www-form-urlencoded":
        return _html_response(
            render_result_page(nonce, success=False),
            nonce=nonce,
            turnstile=False,
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )
    content_length = _parse_content_length(request.headers.get("content-length"))
    if content_length is None:
        return _html_response(
            render_result_page(nonce, success=False),
            nonce=nonce,
            turnstile=False,
            status_code=status.HTTP_411_LENGTH_REQUIRED,
        )
    if content_length > MAX_CHALLENGE_BODY:
        return _html_response(
            render_result_page(nonce, success=False),
            nonce=nonce,
            turnstile=False,
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )

    form = await request.form()
    challenges = form.getlist("challenge")
    responses = form.getlist("cf-turnstile-response")
    if len(challenges) != 1 or len(responses) != 1:
        return _html_response(
            render_result_page(nonce, success=False),
            nonce=nonce,
            turnstile=False,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    challenge = str(challenges[0])
    response_token = str(responses[0])
    if not is_challenge_token(challenge) or not response_token:
        return _html_response(
            render_result_page(nonce, success=False),
            nonce=nonce,
            turnstile=False,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    current = await get_turnstile_challenge(challenge)
    if current.status == "passed":
        result = await apply_turnstile_attempt(challenge, True)
        return _html_response(
            render_result_page(nonce, success=result.status == "passed"),
            nonce=nonce,
            turnstile=False,
            status_code=(
                status.HTTP_200_OK
                if result.status == "passed"
                else status.HTTP_410_GONE
            ),
        )
    if current.status != "active":
        return _html_response(
            render_result_page(nonce, success=False),
            nonce=nonce,
            turnstile=False,
            status_code=status.HTTP_410_GONE,
        )

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            follow_redirects=False,
            headers={"User-Agent": "RelayCat/turnstile"},
        ) as client:
            decision = await verify_turnstile_token(
                client,
                verify_url,
                response_token,
                challenge=challenge,
                expected_hostname=expected_hostname,
            )
    except TurnstileUnavailable:
        logger.warning("Turnstile siteverify Worker is unavailable")
        return _html_response(
            render_challenge_page(
                sitekey,
                nonce,
                challenge=challenge,
                initial_state="unavailable",
            ),
            nonce=nonce,
            turnstile=True,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    result = await apply_turnstile_attempt(challenge, decision.passed)
    if result.status == "passed":
        if result.chat_id is not None and result.message_id is not None:
            try:
                await bot.edit_message_text(
                    chat_id=result.chat_id,
                    message_id=result.message_id,
                    text="验证完成。现在可以发送消息。",
                )
            except TelegramAPIError:
                logger.info(
                    "Verified user %s but could not update Telegram challenge message",
                    result.user_id,
                )
        return _html_response(
            render_result_page(nonce, success=True),
            nonce=nonce,
            turnstile=False,
        )
    if result.status == "failed":
        return _html_response(
            render_challenge_page(
                sitekey,
                nonce,
                challenge=challenge,
                initial_state="failed",
            ),
            nonce=nonce,
            turnstile=True,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return _html_response(
        render_result_page(nonce, success=False),
        nonce=nonce,
        turnstile=False,
        status_code=status.HTTP_410_GONE,
    )
