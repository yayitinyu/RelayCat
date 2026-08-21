from pydantic import SecretStr, field_validator
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
    drop_pending_updates: bool = False

    # Web
    host: str = "0.0.0.0"
    port: int = 8765
    admin_password: SecretStr = SecretStr("admin")
    secret_key: SecretStr = SecretStr("change-me-before-production")
    cookie_secure: bool = False

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

    @property
    def database_url(self) -> str:
        if self.db_url:
            return self.db_url
        normalized_dir = self.data_dir.replace("\\", "/")
        return f"sqlite+aiosqlite:///{normalized_dir}/relaycat.db"


settings = Settings()
