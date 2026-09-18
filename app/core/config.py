import ipaddress
import re
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RELAYCAT_",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    bot_token: SecretStr
    admin_id: int
    bot_enabled: bool = True
    drop_pending_updates: bool = False

    # Web
    host: str = "0.0.0.0"
    port: int = 8765
    admin_password: SecretStr = SecretStr("admin")
    secret_key: SecretStr = SecretStr("change-me-before-production")
    cookie_secure: bool = False
    turnstile_public_url: str | None = None
    turnstile_sitekey: str | None = None
    turnstile_verify_url: str | None = None

    # Database
    data_dir: str = "./data"
    db_url: str | None = None

    # Relay
    enable_forwarding: bool = True

    @field_validator("port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator(
        "turnstile_public_url",
        "turnstile_sitekey",
        "turnstile_verify_url",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("turnstile_public_url")
    @classmethod
    def validate_turnstile_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Turnstile public URL contains an invalid port") from exc
        hostname = (parsed.hostname or "").lower()
        loopback = _is_loopback_hostname(hostname)
        if (
            parsed.scheme.lower() not in ({"http", "https"} if loopback else {"https"})
            or not _is_valid_hostname(hostname)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "Turnstile public URL must be an HTTPS origin without path, "
                "credentials, query, or fragment"
            )
        return value.rstrip("/")

    @field_validator("turnstile_verify_url")
    @classmethod
    def validate_turnstile_verify_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Turnstile verify URL contains an invalid port") from exc
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() != "https"
            or not _is_valid_hostname(hostname)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/", "/siteverify"}
        ):
            raise ValueError(
                "Turnstile verify URL must be an HTTPS Worker endpoint without "
                "credentials, query, or fragment"
            )
        return value.rstrip("/")

    @field_validator("turnstile_sitekey")
    @classmethod
    def validate_turnstile_sitekey(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", value):
            raise ValueError("Turnstile sitekey contains invalid characters")
        return value

    @model_validator(mode="after")
    def validate_turnstile_configuration(self):
        values = (
            self.turnstile_public_url,
            self.turnstile_sitekey,
            self.turnstile_verify_url,
        )
        if any(values) and not all(values):
            raise ValueError(
                "Turnstile public URL, sitekey, and verify URL must be configured together"
            )
        return self

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        normalized_dir = self.data_dir.replace("\\", "/")
        return f"sqlite+aiosqlite:///{normalized_dir}/relaycat.db"

    @property
    def turnstile_configured(self) -> bool:
        return bool(
            self.turnstile_public_url
            and self.turnstile_sitekey
            and self.turnstile_verify_url
        )

    @property
    def turnstile_hostname(self) -> str | None:
        if not self.turnstile_public_url:
            return None
        return urlparse(self.turnstile_public_url).hostname


def _is_loopback_hostname(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_valid_hostname(hostname: str) -> bool:
    if not hostname or len(hostname) > 253:
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    if hostname == "localhost":
        return True
    labels = hostname.rstrip(".").split(".")
    return all(
        0 < len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    )


settings = Settings()
