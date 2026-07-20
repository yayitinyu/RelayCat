import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretDecryptionError(RuntimeError):
    pass


def _fernet() -> Fernet:
    digest = hashlib.sha256(
        f"relaycat-managed-secrets\0{settings.secret_key.get_secret_value()}".encode(
            "utf-8"
        )
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        raise ValueError("Secret cannot be empty")
    return f"fernet:v1:{_fernet().encrypt(value.encode('utf-8')).decode('ascii')}"


def decrypt_secret(value: str) -> str:
    if not value.startswith("fernet:v1:"):
        raise SecretDecryptionError("Unsupported encrypted secret format")
    try:
        return _fernet().decrypt(value.removeprefix("fernet:v1:").encode("ascii")).decode(
            "utf-8"
        )
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise SecretDecryptionError("Could not decrypt the managed secret") from exc
