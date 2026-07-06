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
    return serializer.dumps({"scope": "admin"})


def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        payload: Any = serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(payload, dict) and payload.get("scope") == "admin"
