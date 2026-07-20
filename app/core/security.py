import secrets
from typing import Any

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

COOKIE_NAME = "relaycat_session"
SESSION_MAX_AGE = 60 * 60 * 12

serializer = URLSafeTimedSerializer(
    settings.secret_key.get_secret_value(),
    salt="relaycat-admin-session",
)


def verify_password(candidate: str) -> bool:
    return secrets.compare_digest(
        candidate.encode("utf-8"),
        settings.admin_password.get_secret_value().encode("utf-8"),
    )


def create_session_token() -> str:
    return serializer.dumps({"scope": "admin", "csrf": secrets.token_urlsafe(24)})


def get_session_payload(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload: Any = serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or payload.get("scope") != "admin":
        return None
    return payload


def get_csrf_token(request: Request) -> str:
    payload = get_session_payload(request)
    return str(payload.get("csrf", "")) if payload else ""


def verify_csrf_token(request: Request, candidate: str) -> bool:
    expected = get_csrf_token(request)
    return bool(expected) and secrets.compare_digest(expected, candidate)


def is_authenticated(request: Request) -> bool:
    return get_session_payload(request) is not None
